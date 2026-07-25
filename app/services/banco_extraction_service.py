"""
Extracción OpenRouter → ítems MCQ estilo UNA-Puno → bóveda (temas_estudio + banco_preguntas).

Contrato estricto:
  - Solo opción múltiple A..E (prohibido open/respuesta abierta)
  - materia_id oficial del catálogo (18 Biomédicas)
  - tema_especifico limpio → temas_estudio
  - Validación por esquemas tipados + persistencia transaccional
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Sequence

from app.infrastructure.database.banco_repository import BancoRepository, banco_repository
from app.services.content_service import _openrouter_chat, _strip_code_fences

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
    tema_especifico: str
    enunciado: str
    alternativas: AlternativasUNA
    alternativa_correcta: AlternativaKey
    justificacion: str

    def to_persist_dict(self) -> Dict[str, Any]:
        return {
            "tema_especifico": self.tema_especifico,
            "enunciado": self.enunciado,
            "alternativas": self.alternativas.as_dict(),
            "alternativa_correcta": self.alternativa_correcta,
            "justificacion": self.justificacion,
        }

    @classmethod
    def from_raw(cls, data: Any) -> "ItemBancoGenerado":
        if not isinstance(data, dict):
            raise ItemValidationError("cada ítem debe ser un objeto JSON")

        tema = " ".join(str(data.get("tema_especifico") or "").strip().split())
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
    def from_raw(cls, data: Any) -> "ExtraccionBancoPayload":
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

        if not isinstance(raw_items, list) or not raw_items:
            raise ItemValidationError("lista de ítems vacía")

        items: List[ItemBancoGenerado] = []
        errors: List[str] = []
        for idx, raw in enumerate(raw_items):
            try:
                items.append(ItemBancoGenerado.from_raw(raw))
            except ItemValidationError as exc:
                errors.append(f"item[{idx}]: {exc}")

        if not items:
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
    materia_id: int
    materia_nombre: str
    chunks_procesados: int
    items_validados: int
    persistencia: Dict[str, Any] = field(default_factory=dict)
    chunk_results: List[ChunkExtractionResult] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        items = [x.to_persist_dict() for cr in self.chunk_results for x in cr.items]
        return {
            "materia_id": self.materia_id,
            "materia_nombre": self.materia_nombre,
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

def build_banco_system_prompt(*, max_items: int, materia_nombre: str, materia_codigo: int) -> str:
    return f"""Eres un elaborador oficial de ítems del Examen de Admisión de la Universidad Nacional del Altiplano (UNA-Puno), Área Biomédicas.

Tu ÚNICA salida debe ser JSON válido (sin markdown, sin comentarios) con esta forma exacta:
{{
  "items": [
    {{
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

REGLAS INQUEBRANTABLES:

1) FORMATO CERRADO OBLIGATORIO (ficha óptica UNA)
- PROHIBIDO generar preguntas abiertas, de desarrollo, cloze sin opciones, o "explica con tus palabras".
- Cada ítem DEBE tener exactamente 5 alternativas: A, B, C, D, E.
- alternativa_correcta debe ser una sola letra: A|B|C|D|E.
- Las distractoras deben ser plausibles (errores conceptuales frecuentes), no absurdas.

2) ALINEACIÓN TEMÁTICA FIJA
- Toda la extracción pertenece EXCLUSIVAMENTE a la materia oficial:
  codigo={materia_codigo} · nombre="{materia_nombre}".
- NO inventes otra materia/área. NO uses etiquetas "General", "Bloque", "Varios".
- "tema_especifico" debe ser un subtema académico limpio y estable (2–8 palabras), útil para la tabla temas_estudio.

3) FIDELIDAD AL TEXTO
- Basa cada ítem SOLO en el fragmento proporcionado.
- No inventes datos clínicos, fechas, fórmulas o cifras ausentes en el texto.
- Si el fragmento es insuficiente, genera MENOS ítems (nunca relleno vacío).

4) CALIDAD DE ADMISIÓN
- Estilo examen UNA: enunciado claro, una sola respuesta correcta, sin trampas de redacción ambigua.
- justificacion: sólida, pedagógica, 2–5 oraciones.
- Genera entre 1 y {max_items} ítems de alta calidad (preferir calidad sobre cantidad).
"""


def build_banco_user_prompt(
    *,
    materia_id: int,
    materia_nombre: str,
    materia_codigo: int,
    chunk_text: str,
    chunk_index: int,
    total_chunks: int,
    max_items: int,
) -> str:
    clipped = (chunk_text or "").strip()
    if len(clipped) > 14000:
        clipped = clipped[:14000]
    return (
        f"materia_id={materia_id} (FK oficial catalogo_materias)\n"
        f"materia_codigo={materia_codigo}\n"
        f"materia_nombre={materia_nombre}\n"
        f"fragmento={chunk_index + 1}/{total_chunks}\n"
        f"max_items={max_items}\n\n"
        f"TEXTO FUENTE (ya saneado por la aduana PDF):\n{clipped}"
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
        materia_id: int,
        materia_nombre: str,
        materia_codigo: int,
        max_items: int = 5,
        chunk_index: int = 0,
        total_chunks: int = 1,
    ) -> ChunkExtractionResult:
        if len((chunk_text or "").strip()) < 40:
            return ChunkExtractionResult(
                chunk_index=chunk_index,
                items=[],
                raw_error="Chunk demasiado corto (<40 chars).",
            )

        system_prompt = build_banco_system_prompt(
            max_items=max_items,
            materia_nombre=materia_nombre,
            materia_codigo=materia_codigo,
        )
        user_prompt = build_banco_user_prompt(
            materia_id=materia_id,
            materia_nombre=materia_nombre,
            materia_codigo=materia_codigo,
            chunk_text=chunk_text,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            max_items=max_items,
        )

        try:
            raw = _openrouter_chat(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=4500,
            )
            payload = json.loads(_strip_code_fences(raw))
            validated = ExtraccionBancoPayload.from_raw(payload)
            items = validated.items[: max(1, int(max_items))]
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

    def extract_and_persist_from_chunks(
        self,
        chunks: Sequence[str],
        materia_id: int,
        *,
        max_items_per_chunk: int = 5,
        origen_contenido: str = "pdf",
        fuente: str = "openrouter",
        anio_referencia: Optional[int] = None,
        persist: bool = True,
    ) -> BancoIngestionResult:
        """
        Orquesta: valida materia → extrae por chunk → valida esquema → INSERT transaccional.
        """
        materia = self._repo.fetch_materia(int(materia_id))
        if not materia:
            raise ValueError(
                f"materia_id={materia_id} no existe o está inactiva en catalogo_materias. "
                "Ejecute el seed Tabla 4 Biomédicas."
            )

        materia_nombre = str(materia["nombre"])
        materia_codigo = int(materia["codigo"])
        chunk_list = [c for c in chunks if (c or "").strip()]
        if not chunk_list:
            raise ValueError("No hay chunks de texto para extraer.")

        warnings: List[str] = []
        chunk_results: List[ChunkExtractionResult] = []
        all_items: List[ItemBancoGenerado] = []

        for idx, chunk in enumerate(chunk_list):
            result = self.extract_items_from_chunk(
                chunk,
                materia_id=int(materia_id),
                materia_nombre=materia_nombre,
                materia_codigo=materia_codigo,
                max_items=max_items_per_chunk,
                chunk_index=idx,
                total_chunks=len(chunk_list),
            )
            chunk_results.append(result)
            if result.raw_error:
                warnings.append(f"chunk[{idx}]: {result.raw_error}")
            all_items.extend(result.items)

        persistencia: Dict[str, Any] = {}
        if persist and all_items:
            persistencia = self._repo.persist_items_transactional(
                materia_id=int(materia_id),
                items=[it.to_persist_dict() for it in all_items],
                origen_contenido=origen_contenido,
                fuente=fuente,
                anio_referencia=anio_referencia,
            )
        elif not all_items:
            warnings.append("Ningún ítem válido tras validación / OpenRouter.")

        logger.info(
            "Ingesta bóveda materia_id=%s items=%s insertadas=%s",
            materia_id,
            len(all_items),
            persistencia.get("n_insertadas", 0),
        )
        return BancoIngestionResult(
            materia_id=int(materia_id),
            materia_nombre=materia_nombre,
            chunks_procesados=len(chunk_list),
            items_validados=len(all_items),
            persistencia=persistencia,
            chunk_results=chunk_results,
            warnings=warnings,
        )


banco_extraction_service = BancoExtractionService()


def extract_banco_preguntas_from_chunks(
    chunks: Sequence[str],
    materia_id: int,
    **kwargs: Any,
) -> BancoIngestionResult:
    """API pública: chunks limpios + materia_id oficial → bóveda UNA."""
    return banco_extraction_service.extract_and_persist_from_chunks(
        chunks,
        materia_id,
        **kwargs,
    )
