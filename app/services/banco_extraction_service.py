"""
Extracción OpenRouter → ítems MCQ estilo UNA-Puno → bóveda (temas_estudio + banco_preguntas).

Contrato estricto:
  - Solo opción múltiple A..E (prohibido open/respuesta abierta)
  - Auto-clasificación por materia oficial del catálogo (18 Biomédicas)
  - tema_especifico limpio → temas_estudio
  - Validación por esquemas tipados + persistencia transaccional
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence

from app.infrastructure.database.banco_repository import (
    MATERIA_FALLBACK_NOMBRE,
    BancoRepository,
    banco_repository,
    build_materia_lookup,
    normalize_tema_nombre,
    resolve_materia_id,
)
from app.services.content_service import (
    _openrouter_chat,
    _strip_code_fences,
    image_bytes_to_data_url,
)

logger = logging.getLogger(__name__)

AlternativaKey = Literal["A", "B", "C", "D", "E"]
_KEYS: tuple[str, ...] = ("A", "B", "C", "D", "E")
_BANNED_TEMAS = {"general", "varios", "bloque", "tema", "contenido", "otros", "misc"}


class ItemValidationError(ValueError):
    """Ítem LLM rechazado por el esquema tipado UNA."""


# ---------------------------------------------------------------------------
# Esquemas tipados (validación de salida del LLM)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AlternativasUNA:
    A: str
    B: str
    C: str
    D: str
    E: str

    def as_dict(self) -> Dict[str, str]:
        return {"A": self.A, "B": self.B, "C": self.C, "D": self.D, "E": self.E}

    @classmethod
    def from_raw(cls, raw: Any) -> "AlternativasUNA":
        mapped: Dict[str, str] = {}
        if isinstance(raw, list) and len(raw) == 5:
            mapped = {k: str(raw[i]).strip() for i, k in enumerate(_KEYS)}
        elif isinstance(raw, dict):
            for k, v in raw.items():
                m = re.search(r"[ABCDE]", str(k).strip().upper())
                if m:
                    mapped[m.group(0)] = str(v).strip()
        else:
            raise ItemValidationError("alternativas debe ser objeto A..E o lista de 5 textos")

        if set(mapped.keys()) != set(_KEYS):
            raise ItemValidationError(f"Se requieren claves A..E exactas; llegó {sorted(mapped)}")
        for k in _KEYS:
            if not mapped[k]:
                raise ItemValidationError(f"alternativa {k} vacía")
        return cls(**{k: mapped[k] for k in _KEYS})


@dataclass(frozen=True)
class ItemBancoGenerado:
    materia_nombre: str
    tema_especifico: str
    enunciado: str
    alternativas: AlternativasUNA
    alternativa_correcta: AlternativaKey
    justificacion: str

    def to_persist_dict(self) -> Dict[str, Any]:
        return {
            "materia_nombre": self.materia_nombre,
            "tema_especifico": self.tema_especifico,
            "enunciado": self.enunciado,
            "alternativas": self.alternativas.as_dict(),
            "alternativa_correcta": self.alternativa_correcta,
            "justificacion": self.justificacion,
        }

    @classmethod
    def from_raw(
        cls,
        data: Any,
        *,
        default_materia_nombre: Optional[str] = None,
    ) -> "ItemBancoGenerado":
        if not isinstance(data, dict):
            raise ItemValidationError("cada ítem debe ser un objeto JSON")

        materia_raw = (
            data.get("materia_nombre")
            or data.get("materia")
            or data.get("nombre_materia")
            or default_materia_nombre
            or ""
        )
        materia_nombre = " ".join(str(materia_raw).strip().split())
        if len(materia_nombre) < 2:
            raise ItemValidationError("materia_nombre inválido o ausente")

        tema = normalize_tema_nombre(
            " ".join(str(data.get("tema_especifico") or data.get("tema") or "").strip().split())
        )
        enunciado = " ".join(str(data.get("enunciado") or "").strip().split())
        justificacion = " ".join(str(data.get("justificacion") or "").strip().split())

        if len(tema) < 2 or len(tema) > 200:
            raise ItemValidationError("tema_especifico inválido (2–200 chars)")
        low = tema.lower()
        if low in _BANNED_TEMAS or re.match(r"^(bloque|tema)\s*\d+$", low):
            raise ItemValidationError(f"tema_especifico prohibido: {tema!r}")
        if len(enunciado) < 10:
            raise ItemValidationError("enunciado demasiado corto (<10)")
        if len(justificacion) < 5:
            raise ItemValidationError("justificacion demasiado corta (<5)")

        key_raw = str(data.get("alternativa_correcta") or "").strip().upper()
        m = re.search(r"[ABCDE]", key_raw)
        if not m:
            raise ItemValidationError(f"alternativa_correcta inválida: {key_raw!r}")
        correcta: AlternativaKey = m.group(0)  # type: ignore[assignment]

        alts = AlternativasUNA.from_raw(data.get("alternativas"))
        return cls(
            materia_nombre=materia_nombre,
            tema_especifico=tema,
            enunciado=enunciado,
            alternativas=alts,
            alternativa_correcta=correcta,
            justificacion=justificacion,
        )


@dataclass
class ExtraccionBancoPayload:
    items: List[ItemBancoGenerado]

    @classmethod
    def from_raw(
        cls,
        data: Any,
        *,
        default_materia_nombre: Optional[str] = None,
    ) -> "ExtraccionBancoPayload":
        if isinstance(data, list):
            raw_items = data
        elif isinstance(data, dict):
            raw_items = None
            for key in ("items", "preguntas", "items_banco", "banco", "questions"):
                if key in data:
                    raw_items = data[key]
                    break
            if raw_items is None:
                raise ItemValidationError("JSON sin lista de ítems reconocible")
        else:
            raise ItemValidationError("JSON de extracción inválido")

        if not isinstance(raw_items, list):
            raise ItemValidationError("lista de ítems inválida")
        if not raw_items:
            return cls(items=[])

        items: List[ItemBancoGenerado] = []
        errors: List[str] = []
        for idx, raw in enumerate(raw_items):
            try:
                items.append(
                    ItemBancoGenerado.from_raw(
                        raw, default_materia_nombre=default_materia_nombre
                    )
                )
            except ItemValidationError as exc:
                errors.append(f"item[{idx}]: {exc}")

        if not items and errors:
            raise ItemValidationError(
                "Ningún ítem pasó validación: " + "; ".join(errors[:5])
            )
        return cls(items=items)


@dataclass
class ChunkExtractionResult:
    chunk_index: int
    items: List[ItemBancoGenerado] = field(default_factory=list)
    raw_error: Optional[str] = None


@dataclass
class BancoIngestionResult:
    materia_id: Optional[int]
    materia_nombre: str
    chunks_procesados: int
    items_validados: int
    persistencia: Dict[str, Any] = field(default_factory=dict)
    chunk_results: List[ChunkExtractionResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    auto_clasificar: bool = True

    def to_dict(self) -> Dict[str, Any]:
        items = [x.to_persist_dict() for cr in self.chunk_results for x in cr.items]
        return {
            "materia_id": self.materia_id,
            "materia_nombre": self.materia_nombre,
            "auto_clasificar": self.auto_clasificar,
            "chunks_procesados": self.chunks_procesados,
            "items_validados": self.items_validados,
            "persistencia": self.persistencia,
            "warnings": self.warnings,
            "chunk_errors": [
                {"chunk_index": c.chunk_index, "error": c.raw_error}
                for c in self.chunk_results
                if c.raw_error
            ],
            "items": items,
        }


# ---------------------------------------------------------------------------
# Prompt engineering UNA-Puno
# ---------------------------------------------------------------------------

_MAX_ITEMS_POR_CHUNK = 5


def _format_materias_oficiales(materias: Sequence[Dict[str, Any]]) -> str:
    parts = [
        f"{int(m['codigo']):02d}. {m['nombre']}"
        for m in materias
        if m.get("nombre")
    ]
    return ", ".join(parts)


def build_banco_system_prompt(
    *,
    materias_oficiales: Sequence[Dict[str, Any]],
    temas_existentes: Optional[Sequence[str]] = None,
    materia_fija_nombre: Optional[str] = None,
    modo_imagen: bool = False,
) -> str:
    lista_materias = _format_materias_oficiales(materias_oficiales) or "(catálogo vacío)"

    temas = [t.strip() for t in (temas_existentes or []) if str(t).strip()]
    fuente_label = "esta foto del compendio" if modo_imagen else "este texto"
    if temas:
        lista = ", ".join(temas[:80])
        if len(temas) > 80:
            lista += f", … (+{len(temas) - 80} más)"
        exclusion_block = (
            f'El alumno ya tiene preguntas sobre los siguientes temas: [{lista}]. '
            f"Tu objetivo es buscar conceptos, variables o información NUEVA en {fuente_label}. "
            "NO generes preguntas sobre los temas listados a menos que el contenido presente "
            "un enfoque completamente distinto o más avanzado."
        )
    else:
        exclusion_block = (
            "El alumno aún no tiene temas cargados: "
            f"puedes extraer libremente los conceptos clave de {fuente_label}."
        )

    if materia_fija_nombre:
        clasificacion_block = (
            f'Clasifica cada pregunta en la materia "{materia_fija_nombre}" '
            "(modo curaduría forzada). Usa exactamente ese nombre en materia_nombre."
        )
    else:
        orden_hint = (
            "el orden de las fotos"
            if modo_imagen
            else "el orden del PDF"
        )
        clasificacion_block = (
            "Para cada pregunta que generes, analiza su contenido y clasifícala "
            "obligatoriamente en una de las materias oficiales de la lista provista. "
            "Además, asigna un 'tema' normalizado (campo tema_especifico). "
            "Un mismo fragmento puede mezclar materias (p. ej. Biología y luego Química): "
            f"asigna cada ítem a la materia correcta según su contenido, no según {orden_hint}."
        )

    if modo_imagen:
        densidad_block = (
            "Analiza la densidad de información visible en la foto "
            "(texto impreso, apuntes, diagramas con rótulos legibles).\n"
            f"- Genera entre 0 y {_MAX_ITEMS_POR_CHUNK} preguntas como máximo por foto,\n"
            "  dependiendo de cuántos conceptos clave NUEVOS encuentres.\n"
            "- Si la imagen está borrosa, vacía o sin contenido académico, "
            "devuelve una lista vacía []."
        )
        fidelidad_block = (
            "Basa cada ítem SOLO en lo legible en la imagen adjunta.\n"
            "- Transcribe con fidelidad fórmulas, cifras y términos visibles.\n"
            "- No inventes datos clínicos, fechas, fórmulas o cifras que no se lean en la foto."
        )
    else:
        densidad_block = (
            "Analiza la densidad de información de este fragmento de texto.\n"
            f"- Genera entre 0 y {_MAX_ITEMS_POR_CHUNK} preguntas como máximo por fragmento,\n"
            "  dependiendo de cuántos conceptos clave NUEVOS encuentres.\n"
            "- Si el texto es relleno o no aporta información útil, "
            "devuelve una lista vacía []."
        )
        fidelidad_block = (
            "Basa cada ítem SOLO en el fragmento proporcionado.\n"
            "- No inventes datos clínicos, fechas, fórmulas o cifras ausentes en el texto."
        )

    return f"""Eres un elaborador oficial de ítems del Examen de Admisión de la Universidad Nacional del Altiplano (UNA-Puno), Área Biomédicas.

MATERIAS OFICIALES (usa EXACTAMENTE uno de estos nombres en materia_nombre):
[{lista_materias}]

Tu ÚNICA salida debe ser JSON válido (sin markdown, sin comentarios) con esta forma exacta:
{{
  "items": [
    {{
      "materia_nombre": "Biología y Anatomía",
      "tema_especifico": "nombre limpio del subtema (ej. Tejido epitelial)",
      "enunciado": "pregunta cerrada estilo ficha óptica UNA",
      "alternativas": {{
        "A": "...",
        "B": "...",
        "C": "...",
        "D": "...",
        "E": "..."
      }},
      "alternativa_correcta": "C",
      "justificacion": "explicación pedagógica breve de por qué C es correcta y por qué las otras fallan"
    }}
  ]
}}

Si no hay conceptos nuevos útiles, responde exactamente: {{"items": []}}

REGLAS INQUEBRANTABLES:

1) FORMATO CERRADO OBLIGATORIO (ficha óptica UNA)
- PROHIBIDO generar preguntas abiertas, de desarrollo, cloze sin opciones, o "explica con tus palabras".
- Cada ítem DEBE tener exactamente 5 alternativas: A, B, C, D, E.
- alternativa_correcta debe ser una sola letra: A|B|C|D|E.
- Las distractoras deben ser plausibles (errores conceptuales frecuentes), no absurdas.

2) AUTO-CLASIFICACIÓN POR MATERIA Y TEMA
- {clasificacion_block}
- PROHIBIDO inventar materias fuera de la lista (no uses "General", "Bloque", "Varios", "Cultura General").
- Si el contenido es ambiguo, elige la materia oficial más cercana de la lista.
- REGLA DE TEMAS: "tema_especifico" debe ser un concepto técnico directo (2–8 palabras),
  SIN artículos (el, la, los, las, un, una) y SIN palabras de relleno.
  Correctos: "Osmosis", "Tejido Epitelial", "Cinemática".
  Incorrectos: "La osmosis", "El tejido", "Preguntas de fisica", "Tema de biología".
- El nombre debe ser estable y reutilizable en la tabla temas_estudio.

3) DELTA / EXCLUSIÓN DE TEMAS YA CUBIERTOS
- {exclusion_block}

4) DENSIDAD DE INFORMACIÓN (yield dinámico)
- No tienes un límite fijo de preguntas.
- {densidad_block}

5) FIDELIDAD AL CONTENIDO
- {fidelidad_block}

6) CALIDAD DE ADMISIÓN
- Estilo examen UNA: enunciado claro, una sola respuesta correcta, sin trampas de redacción ambigua.
- justificacion: sólida, pedagógica, 2–5 oraciones.
"""


def build_banco_user_prompt(
    *,
    chunk_text: str,
    chunk_index: int,
    total_chunks: int,
    temas_existentes: Optional[Sequence[str]] = None,
    materias_oficiales: Optional[Sequence[Dict[str, Any]]] = None,
    materia_fija_nombre: Optional[str] = None,
) -> str:
    clipped = (chunk_text or "").strip()
    if len(clipped) > 14000:
        clipped = clipped[:14000]
    temas = [t.strip() for t in (temas_existentes or []) if str(t).strip()]
    temas_line = (
        f"temas_ya_cubiertos={len(temas)} (ver system prompt)\n"
        if temas
        else "temas_ya_cubiertos=0 (primera extracción libre)\n"
    )
    materias_line = ""
    if materias_oficiales:
        materias_line = (
            f"materias_oficiales={_format_materias_oficiales(materias_oficiales)}\n"
        )
    fija_line = (
        f"materia_forzada={materia_fija_nombre}\n" if materia_fija_nombre else ""
    )
    return (
        f"fragmento={chunk_index + 1}/{total_chunks}\n"
        f"{materias_line}"
        f"{fija_line}"
        f"{temas_line}\n"
        f"TEXTO FUENTE (ya saneado por la aduana PDF):\n{clipped}"
    )


def build_banco_user_prompt_imagen(
    *,
    image_index: int,
    total_images: int,
    temas_existentes: Optional[Sequence[str]] = None,
    materias_oficiales: Optional[Sequence[Dict[str, Any]]] = None,
    materia_fija_nombre: Optional[str] = None,
    nombre_archivo: Optional[str] = None,
) -> str:
    temas = [t.strip() for t in (temas_existentes or []) if str(t).strip()]
    temas_line = (
        f"temas_ya_cubiertos={len(temas)} (ver system prompt)\n"
        if temas
        else "temas_ya_cubiertos=0 (primera extracción libre)\n"
    )
    materias_line = ""
    if materias_oficiales:
        materias_line = (
            f"materias_oficiales={_format_materias_oficiales(materias_oficiales)}\n"
        )
    fija_line = (
        f"materia_forzada={materia_fija_nombre}\n" if materia_fija_nombre else ""
    )
    archivo_line = (
        f"archivo={nombre_archivo}\n" if (nombre_archivo or "").strip() else ""
    )
    return (
        f"foto={image_index + 1}/{total_images}\n"
        f"{archivo_line}"
        f"{materias_line}"
        f"{fija_line}"
        f"{temas_line}\n"
        "IMAGEN FUENTE: foto del compendio físico de academia adjunta.\n"
        "Lee el texto visible (impreso o manuscrito legible) y genera ítems MCQ A–E."
    )


# ---------------------------------------------------------------------------
# Servicio
# ---------------------------------------------------------------------------

class BancoExtractionService:
    """Conecta chunks limpios → OpenRouter MCQ → persistencia en bóveda."""

    def __init__(self, repository: Optional[BancoRepository] = None) -> None:
        self._repo = repository or banco_repository

    def extract_items_from_chunk(
        self,
        chunk_text: str,
        *,
        materias_oficiales: Sequence[Dict[str, Any]],
        chunk_index: int = 0,
        total_chunks: int = 1,
        temas_existentes: Optional[Sequence[str]] = None,
        max_items: int = _MAX_ITEMS_POR_CHUNK,
        materia_fija_nombre: Optional[str] = None,
        # Compat firma antigua (ignorados)
        materia_id: Optional[int] = None,
        materia_nombre: Optional[str] = None,
        materia_codigo: Optional[int] = None,
    ) -> ChunkExtractionResult:
        del materia_id, materia_codigo
        if materia_fija_nombre is None and materia_nombre:
            materia_fija_nombre = materia_nombre

        if len((chunk_text or "").strip()) < 40:
            return ChunkExtractionResult(
                chunk_index=chunk_index,
                items=[],
                raw_error="Chunk demasiado corto (<40 chars).",
            )

        cap = max(0, min(int(max_items), _MAX_ITEMS_POR_CHUNK))
        system_prompt = build_banco_system_prompt(
            materias_oficiales=materias_oficiales,
            temas_existentes=temas_existentes,
            materia_fija_nombre=materia_fija_nombre,
        )
        user_prompt = build_banco_user_prompt(
            chunk_text=chunk_text,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            temas_existentes=temas_existentes,
            materias_oficiales=materias_oficiales,
            materia_fija_nombre=materia_fija_nombre,
        )

        try:
            raw = _openrouter_chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=4500,
            )
            payload = json.loads(_strip_code_fences(raw))
            validated = ExtraccionBancoPayload.from_raw(
                payload, default_materia_nombre=materia_fija_nombre
            )
            items = validated.items[:cap] if cap else []
            return ChunkExtractionResult(chunk_index=chunk_index, items=items)
        except Exception as exc:
            logger.warning(
                "Extracción banco falló chunk=%s: %s: %s",
                chunk_index,
                type(exc).__name__,
                exc,
            )
            return ChunkExtractionResult(
                chunk_index=chunk_index,
                items=[],
                raw_error=f"{type(exc).__name__}: {exc}",
            )

    def extract_items_from_image(
        self,
        image_bytes: bytes,
        *,
        materias_oficiales: Sequence[Dict[str, Any]],
        image_index: int = 0,
        total_images: int = 1,
        temas_existentes: Optional[Sequence[str]] = None,
        max_items: int = _MAX_ITEMS_POR_CHUNK,
        materia_fija_nombre: Optional[str] = None,
        nombre_archivo: Optional[str] = None,
    ) -> ChunkExtractionResult:
        """Extrae MCQ desde una foto (visión multimodal OpenRouter)."""
        if not image_bytes:
            return ChunkExtractionResult(
                chunk_index=image_index,
                items=[],
                raw_error="Imagen vacía.",
            )

        cap = max(0, min(int(max_items), _MAX_ITEMS_POR_CHUNK))
        system_prompt = build_banco_system_prompt(
            materias_oficiales=materias_oficiales,
            temas_existentes=temas_existentes,
            materia_fija_nombre=materia_fija_nombre,
            modo_imagen=True,
        )
        user_prompt = build_banco_user_prompt_imagen(
            image_index=image_index,
            total_images=total_images,
            temas_existentes=temas_existentes,
            materias_oficiales=materias_oficiales,
            materia_fija_nombre=materia_fija_nombre,
            nombre_archivo=nombre_archivo,
        )

        try:
            data_url = image_bytes_to_data_url(image_bytes)
            raw = _openrouter_chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=4500,
                image_data_url=data_url,
            )
            payload = json.loads(_strip_code_fences(raw))
            validated = ExtraccionBancoPayload.from_raw(
                payload, default_materia_nombre=materia_fija_nombre
            )
            items = validated.items[:cap] if cap else []
            return ChunkExtractionResult(chunk_index=image_index, items=items)
        except Exception as exc:
            logger.warning(
                "Extracción banco (imagen) falló foto=%s: %s: %s",
                image_index,
                type(exc).__name__,
                exc,
            )
            return ChunkExtractionResult(
                chunk_index=image_index,
                items=[],
                raw_error=f"{type(exc).__name__}: {exc}",
            )

    def extract_and_persist_from_chunks(
        self,
        chunks: Sequence[str],
        materia_id: Optional[int] = None,
        *,
        auto_clasificar: bool = True,
        max_items_per_chunk: int = _MAX_ITEMS_POR_CHUNK,
        origen_contenido: str = "pdf",
        fuente: str = "openrouter",
        anio_referencia: Optional[int] = None,
        nombre_archivo_fuente: Optional[str] = None,
        propietario_usuario_id: Optional[int] = None,
        persist: bool = True,
        pause_between_chunks_s: float = 0.35,
        on_chunk_progress: Optional[Callable[[int, int, str], None]] = None,
        max_items_total: Optional[int] = None,
    ) -> BancoIngestionResult:
        """
        Orquesta: catálogo → delta temas → extracción (auto-materia) → INSERT.

        ``auto_clasificar=True`` (estudiante): el LLM asigna materia_nombre por ítem.
        ``auto_clasificar=False`` + ``materia_id`` (admin): fuerza esa materia.
        ``materia_id`` con auto_clasificar: solo fallback si el LLM inventa nombre.
        """
        del max_items_total

        materias = self._repo.fetch_materias()
        if not materias:
            raise ValueError(
                "Catálogo de materias vacío. Ejecute el seed Tabla 4 Biomédicas."
            )
        materia_lookup = build_materia_lookup(materias)

        materia_fija: Optional[Dict[str, Any]] = None
        if not auto_clasificar:
            if materia_id is None:
                raise ValueError("auto_clasificar=False requiere materia_id.")
            materia_fija = self._repo.fetch_materia(int(materia_id))
            if not materia_fija:
                raise ValueError(
                    f"materia_id={materia_id} no existe o está inactiva en catalogo_materias."
                )

        fallback_id = int(materia_id) if materia_id is not None else None
        if fallback_id is None:
            fallback_id = resolve_materia_id(MATERIA_FALLBACK_NOMBRE, materia_lookup)

        chunk_list = [c for c in chunks if (c or "").strip()]
        if not chunk_list:
            raise ValueError("No hay chunks de texto para extraer.")

        owner = int(propietario_usuario_id) if propietario_usuario_id is not None else None
        try:
            # Delta global del dueño (todas las materias) en modo auto;
            # en modo forzado, solo la materia curada.
            temas_previos = self._repo.fetch_temas_cubiertos_por_propietario(
                propietario_usuario_id=owner,
                materia_id=int(materia_fija["id_materia"]) if materia_fija else None,
            )
        except Exception as exc:
            logger.warning("No se pudieron cargar temas previos: %s", exc)
            temas_previos = []

        temas_excluidos: List[str] = list(temas_previos)
        temas_excluidos_norm = {normalize_tema_nombre(t).lower() for t in temas_excluidos}

        warnings: List[str] = []
        if auto_clasificar:
            warnings.append(
                "Auto-clasificación activa: cada pregunta se enruta a su materia oficial."
            )
        if temas_previos:
            warnings.append(
                f"Delta Ingestion: {len(temas_previos)} tema(s) ya cubiertos "
                f"se pasaron al LLM para evitar redundancia."
            )
        else:
            warnings.append("Sin temas previos del dueño: extracción libre.")

        chunk_results: List[ChunkExtractionResult] = []
        all_items: List[ItemBancoGenerado] = []
        archivo = (nombre_archivo_fuente or "").strip()[:255] or None
        per_chunk = max(0, min(int(max_items_per_chunk), _MAX_ITEMS_POR_CHUNK))
        total = len(chunk_list)
        fija_nombre = str(materia_fija["nombre"]) if materia_fija else None

        for idx, chunk in enumerate(chunk_list):
            if on_chunk_progress is not None:
                on_chunk_progress(
                    idx,
                    total,
                    f"Analizando fragmento {idx + 1}/{total} con IA "
                    f"(clasificando materias · excluyendo {len(temas_excluidos)} tema(s))…",
                )

            result = self.extract_items_from_chunk(
                chunk,
                materias_oficiales=materias,
                max_items=per_chunk,
                chunk_index=idx,
                total_chunks=total,
                temas_existentes=temas_excluidos,
                materia_fija_nombre=fija_nombre,
            )
            chunk_results.append(result)
            if result.raw_error:
                warnings.append(f"chunk[{idx}]: {result.raw_error}")

            for item in result.items:
                # Si modo forzado, normaliza materia al catálogo fijo.
                if fija_nombre:
                    item = ItemBancoGenerado(
                        materia_nombre=fija_nombre,
                        tema_especifico=item.tema_especifico,
                        enunciado=item.enunciado,
                        alternativas=item.alternativas,
                        alternativa_correcta=item.alternativa_correcta,
                        justificacion=item.justificacion,
                    )
                all_items.append(item)
                key = normalize_tema_nombre(item.tema_especifico).lower()
                if key and key not in temas_excluidos_norm:
                    temas_excluidos_norm.add(key)
                    temas_excluidos.append(item.tema_especifico)

            if idx < total - 1 and pause_between_chunks_s > 0:
                time.sleep(float(pause_between_chunks_s))

        if on_chunk_progress is not None:
            on_chunk_progress(total, total, "Persistiendo preguntas en tu bóveda…")

        persistencia: Dict[str, Any] = {}
        if persist and all_items:
            persistencia = self._repo.persist_items_transactional(
                items=[it.to_persist_dict() for it in all_items],
                materia_id=fallback_id,
                materia_lookup=materia_lookup,
                origen_contenido=origen_contenido,
                fuente=fuente,
                anio_referencia=anio_referencia,
                nombre_archivo_fuente=archivo,
                propietario_usuario_id=owner,
            )
            n_fb = int(persistencia.get("n_fallback_materia") or 0)
            if n_fb:
                warnings.append(
                    f"{n_fb} ítem(s) usaron materia fallback "
                    f"(«{MATERIA_FALLBACK_NOMBRE}» o la seleccionada) "
                    "porque el LLM inventó un nombre fuera del catálogo."
                )
        elif not all_items:
            warnings.append(
                "Ningún ítem nuevo: el PDF no aportó conceptos útiles "
                "o ya estaban cubiertos (delta)."
            )

        label = (
            fija_nombre
            if fija_nombre
            else "multi-materia (auto)"
        )
        logger.info(
            "Ingesta bóveda modo=%s archivo=%s owner=%s items=%s insertadas=%s materias=%s",
            "fijo" if fija_nombre else "auto",
            archivo,
            owner,
            len(all_items),
            persistencia.get("n_insertadas", 0),
            persistencia.get("materias_tocadas"),
        )
        return BancoIngestionResult(
            materia_id=fallback_id if not fija_nombre else int(materia_fija["id_materia"]),
            materia_nombre=label,
            chunks_procesados=len(chunk_results),
            items_validados=len(all_items),
            persistencia=persistencia,
            chunk_results=chunk_results,
            warnings=warnings,
            auto_clasificar=auto_clasificar and not bool(fija_nombre),
        )

    def extract_and_persist_from_images(
        self,
        images: Sequence[Dict[str, Any]],
        materia_id: Optional[int] = None,
        *,
        auto_clasificar: bool = True,
        max_items_per_image: int = _MAX_ITEMS_POR_CHUNK,
        origen_contenido: str = "imagen",
        fuente: str = "openrouter",
        anio_referencia: Optional[int] = None,
        nombre_archivo_fuente: Optional[str] = None,
        propietario_usuario_id: Optional[int] = None,
        persist: bool = True,
        pause_between_images_s: float = 0.4,
        on_image_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> BancoIngestionResult:
        """
        Orquesta fotos → OpenRouter visión → misma persistencia/auto-clasificación.

        Cada elemento de ``images``: ``{"bytes": bytes, "nombre": str}``.
        """
        materias = self._repo.fetch_materias()
        if not materias:
            raise ValueError(
                "Catálogo de materias vacío. Ejecute el seed Tabla 4 Biomédicas."
            )
        materia_lookup = build_materia_lookup(materias)

        materia_fija: Optional[Dict[str, Any]] = None
        if not auto_clasificar:
            if materia_id is None:
                raise ValueError("auto_clasificar=False requiere materia_id.")
            materia_fija = self._repo.fetch_materia(int(materia_id))
            if not materia_fija:
                raise ValueError(
                    f"materia_id={materia_id} no existe o está inactiva en catalogo_materias."
                )

        fallback_id = int(materia_id) if materia_id is not None else None
        if fallback_id is None:
            fallback_id = resolve_materia_id(MATERIA_FALLBACK_NOMBRE, materia_lookup)

        image_list = [
            img
            for img in images
            if isinstance(img, dict) and img.get("bytes")
        ]
        if not image_list:
            raise ValueError("No hay imágenes para extraer.")

        owner = int(propietario_usuario_id) if propietario_usuario_id is not None else None
        try:
            temas_previos = self._repo.fetch_temas_cubiertos_por_propietario(
                propietario_usuario_id=owner,
                materia_id=int(materia_fija["id_materia"]) if materia_fija else None,
            )
        except Exception as exc:
            logger.warning("No se pudieron cargar temas previos: %s", exc)
            temas_previos = []

        temas_excluidos: List[str] = list(temas_previos)
        temas_excluidos_norm = {normalize_tema_nombre(t).lower() for t in temas_excluidos}

        warnings: List[str] = [
            "Ingesta multimodal: la IA lee fotos del compendio (visión OpenRouter)."
        ]
        if auto_clasificar:
            warnings.append(
                "Auto-clasificación activa: cada pregunta se enruta a su materia oficial."
            )
        if temas_previos:
            warnings.append(
                f"Delta Ingestion: {len(temas_previos)} tema(s) ya cubiertos "
                f"se pasaron al LLM para evitar redundancia."
            )
        else:
            warnings.append("Sin temas previos del dueño: extracción libre.")

        chunk_results: List[ChunkExtractionResult] = []
        all_items: List[ItemBancoGenerado] = []
        archivo = (nombre_archivo_fuente or "").strip()[:255] or None
        per_img = max(0, min(int(max_items_per_image), _MAX_ITEMS_POR_CHUNK))
        total = len(image_list)
        fija_nombre = str(materia_fija["nombre"]) if materia_fija else None

        for idx, img in enumerate(image_list):
            nombre_img = str(img.get("nombre") or archivo or f"foto_{idx + 1}.jpg")
            if on_image_progress is not None:
                on_image_progress(
                    idx,
                    total,
                    f"Analizando foto {idx + 1}/{total} con IA "
                    f"(clasificando materias · excluyendo {len(temas_excluidos)} tema(s))…",
                )

            result = self.extract_items_from_image(
                bytes(img["bytes"]),
                materias_oficiales=materias,
                max_items=per_img,
                image_index=idx,
                total_images=total,
                temas_existentes=temas_excluidos,
                materia_fija_nombre=fija_nombre,
                nombre_archivo=nombre_img,
            )
            chunk_results.append(result)
            if result.raw_error:
                warnings.append(f"foto[{idx}]: {result.raw_error}")

            for item in result.items:
                if fija_nombre:
                    item = ItemBancoGenerado(
                        materia_nombre=fija_nombre,
                        tema_especifico=item.tema_especifico,
                        enunciado=item.enunciado,
                        alternativas=item.alternativas,
                        alternativa_correcta=item.alternativa_correcta,
                        justificacion=item.justificacion,
                    )
                all_items.append(item)
                key = normalize_tema_nombre(item.tema_especifico).lower()
                if key and key not in temas_excluidos_norm:
                    temas_excluidos_norm.add(key)
                    temas_excluidos.append(item.tema_especifico)

            if idx < total - 1 and pause_between_images_s > 0:
                time.sleep(float(pause_between_images_s))

        if on_image_progress is not None:
            on_image_progress(total, total, "Persistiendo preguntas en tu bóveda…")

        persistencia: Dict[str, Any] = {}
        if persist and all_items:
            persistencia = self._repo.persist_items_transactional(
                items=[it.to_persist_dict() for it in all_items],
                materia_id=fallback_id,
                materia_lookup=materia_lookup,
                origen_contenido=origen_contenido,
                fuente=fuente,
                anio_referencia=anio_referencia,
                nombre_archivo_fuente=archivo,
                propietario_usuario_id=owner,
            )
            n_fb = int(persistencia.get("n_fallback_materia") or 0)
            if n_fb:
                warnings.append(
                    f"{n_fb} ítem(s) usaron materia fallback "
                    f"(«{MATERIA_FALLBACK_NOMBRE}» o la seleccionada) "
                    "porque el LLM inventó un nombre fuera del catálogo."
                )
        elif not all_items:
            warnings.append(
                "Ningún ítem nuevo: la(s) foto(s) no aportaron conceptos útiles "
                "o ya estaban cubiertos (delta)."
            )

        label = fija_nombre if fija_nombre else "multi-materia (auto)"
        logger.info(
            "Ingesta imagen modo=%s archivo=%s owner=%s items=%s insertadas=%s materias=%s",
            "fijo" if fija_nombre else "auto",
            archivo,
            owner,
            len(all_items),
            persistencia.get("n_insertadas", 0),
            persistencia.get("materias_tocadas"),
        )
        return BancoIngestionResult(
            materia_id=fallback_id if not fija_nombre else int(materia_fija["id_materia"]),
            materia_nombre=label,
            chunks_procesados=len(chunk_results),
            items_validados=len(all_items),
            persistencia=persistencia,
            chunk_results=chunk_results,
            warnings=warnings,
            auto_clasificar=auto_clasificar and not bool(fija_nombre),
        )


banco_extraction_service = BancoExtractionService()


def extract_banco_preguntas_from_chunks(
    chunks: Sequence[str],
    materia_id: Optional[int] = None,
    **kwargs: Any,
) -> BancoIngestionResult:
    """API pública: chunks limpios → bóveda UNA (auto-clasificación por defecto)."""
    return banco_extraction_service.extract_and_persist_from_chunks(
        chunks,
        materia_id,
        **kwargs,
    )


def extract_banco_preguntas_from_images(
    images: Sequence[Dict[str, Any]],
    materia_id: Optional[int] = None,
    **kwargs: Any,
) -> BancoIngestionResult:
    """API pública: fotos JPG/PNG → bóveda UNA (misma auto-clasificación)."""
    return banco_extraction_service.extract_and_persist_from_images(
        images,
        materia_id,
        **kwargs,
    )
