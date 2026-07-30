"""
Persistencia de la bóveda UNA: temas_estudio + banco_preguntas.

Inserciones idempotentes:
  - tema: UNIQUE (materia_id, slug) → upsert
  - pregunta: UNIQUE (hash_contenido) → ON CONFLICT DO NOTHING
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence

from psycopg2.extras import Json

from app.infrastructure.database.connection import DatabaseConnection, db_connection


def slugify_tema(nombre: str, *, max_len: int = 200) -> str:
    """
    Slug estable para UNIQUE (materia_id, slug).

    Normalización agresiva Fase 6:
      - quita artículos iniciales (el/la/los/las/un/una/unos/unas)
      - NFKD + sin diacríticos → "Ósmosis" y "La ósmosis" → mismo slug
    """
    text = unicodedata.normalize("NFKD", (nombre or "").strip())
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("M"))
    text = text.lower().strip()
    text = re.sub(
        r"^(el|la|los|las|un|una|unos|unas)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[^a-z0-9áéíóúüñ]+", "-", text, flags=re.IGNORECASE)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        text = "tema"
    return text[:max_len]


def normalize_tema_nombre(nombre: str, *, max_len: int = 200) -> str:
    """Limpia nombre de tema para persistencia (sin artículos iniciales)."""
    text = " ".join(str(nombre or "").strip().split())
    text = re.sub(
        r"^(el|la|los|las|un|una|unos|unas)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    if not text:
        text = str(nombre or "").strip() or "Tema"
    return text[:max_len]


def _fold_materia_key(nombre: str) -> str:
    """Clave case/diacrítico-insensible para match de materias."""
    text = unicodedata.normalize("NFKD", (nombre or "").strip())
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("M"))
    text = re.sub(r"\s+", " ", text.lower()).strip()
    return text


# Alias frecuentes del LLM / guías → nombre oficial del catálogo UNA.
_MATERIA_ALIASES: Dict[str, str] = {
    "biologia": "Biología y Anatomía",
    "anatomia": "Biología y Anatomía",
    "biologia y anatomia": "Biología y Anatomía",
    "anatomia y biologia": "Biología y Anatomía",
    "quimica": "Química",
    "fisica": "Física",
    "aritmetica": "Aritmética",
    "algebra": "Álgebra",
    "geometria": "Geometría",
    "trigonometria": "Trigonometría",
    "psicologia": "Psicología y Filosofía",
    "filosofia": "Psicología y Filosofía",
    "psicologia y filosofia": "Psicología y Filosofía",
    "geografia": "Geografía",
    "educacion civica": "Educación Cívica",
    "civica": "Educación Cívica",
    "economia": "Economía",
    "lenguaje": "Comunicación",
    "comunicacion": "Comunicación",
    "literatura": "Literatura",
    "razonamiento matematico": "Razonamiento Matemático",
    "razonamiento verbal": "Razonamiento Verbal",
    "ingles": "Inglés",
    "quechua": "Quechua y aimara",
    "aimara": "Quechua y aimara",
    "quechua y aimara": "Quechua y aimara",
    "cultura general": "Razonamiento Verbal",
    "general": "Razonamiento Verbal",
}

# Fallback cuando el LLM inventa una materia fuera del catálogo.
MATERIA_FALLBACK_NOMBRE = "Razonamiento Verbal"


def build_materia_lookup(materias: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """
    Diccionario en memoria: nombre_normalizado → id_materia.
    Incluye aliases comunes (Biología → Biología y Anatomía).
    """
    by_fold: Dict[str, int] = {}
    official_by_fold: Dict[str, str] = {}
    for m in materias:
        mid = int(m["id_materia"])
        nombre = str(m["nombre"])
        key = _fold_materia_key(nombre)
        by_fold[key] = mid
        official_by_fold[key] = nombre

    for alias, official in _MATERIA_ALIASES.items():
        target_key = _fold_materia_key(official)
        if target_key in by_fold:
            by_fold[_fold_materia_key(alias)] = by_fold[target_key]
    return by_fold


def resolve_materia_id(
    materia_nombre: Optional[str],
    lookup: Dict[str, int],
    *,
    fallback_id: Optional[int] = None,
) -> Optional[int]:
    """Resuelve id por nombre (case-insensitive). None si no hay match ni fallback."""
    key = _fold_materia_key(materia_nombre or "")
    if key and key in lookup:
        return int(lookup[key])
    if fallback_id is not None:
        return int(fallback_id)
    fb = lookup.get(_fold_materia_key(MATERIA_FALLBACK_NOMBRE))
    return int(fb) if fb is not None else None


def hash_contenido_pregunta(
    enunciado: str,
    alternativas: Dict[str, str],
    alternativa_correcta: str,
) -> str:
    """SHA-256 canónico para deduplicar ítems en la bóveda."""
    payload = {
        "enunciado": " ".join((enunciado or "").split()),
        "alternativas": {k: " ".join(str(alternativas.get(k, "")).split()) for k in ("A", "B", "C", "D", "E")},
        "alternativa_correcta": (alternativa_correcta or "").strip().upper(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class BancoRepository:
    """CRUD mínimo de catálogo → temas → banco (scoped a materia oficial)."""

    def __init__(self, connection: Optional[DatabaseConnection] = None) -> None:
        self._connection = connection or db_connection

    def fetch_materias(
        self,
        *,
        area_examen: str = "BIOMEDICAS",
        solo_activas: bool = True,
    ) -> List[Dict[str, Any]]:
        """Catálogo oficial para dropdowns de la UI."""
        query = """
        SELECT id_materia, codigo, nombre, cantidad_preguntas, factor_ponderacion, area_examen
        FROM catalogo_materias
        WHERE area_examen = %s
        """
        if solo_activas:
            query += " AND activo"
        query += " ORDER BY codigo;"
        with self._connection.get_cursor() as cur:
            cur.execute(query, (area_examen,))
            return [dict(r) for r in cur.fetchall()]

    def fetch_temas_by_materia(self, materia_id: int) -> List[Dict[str, Any]]:
        """Temas activos ligados a una materia (práctica enfocada)."""
        query = """
        SELECT id_tema, materia_id, nombre, slug, origen_contenido
        FROM temas_estudio
        WHERE materia_id = %s AND activo
        ORDER BY nombre;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, (int(materia_id),))
            return [dict(r) for r in cur.fetchall()]

    def fetch_temas_cubiertos_por_propietario(
        self,
        *,
        propietario_usuario_id: Optional[int],
        materia_id: Optional[int] = None,
    ) -> List[str]:
        """
        Nombres de temas donde el dueño ya tiene ítems activos (Delta Ingestion).

        - propietario_usuario_id=None → temas del banco oficial
        - ID → temas de las guías privadas del alumno
        - materia_id=None → todas las materias del catálogo (auto-clasificación)
        """
        clauses = ["p.activa", "t.activo"]
        params: list[Any] = []

        if materia_id is not None:
            clauses.append("t.materia_id = %s")
            params.append(int(materia_id))

        if propietario_usuario_id is None:
            clauses.append("p.propietario_usuario_id IS NULL")
        else:
            clauses.append("p.propietario_usuario_id = %s")
            params.append(int(propietario_usuario_id))

        query = f"""
        SELECT DISTINCT t.nombre
        FROM banco_preguntas p
        JOIN temas_estudio t ON t.id_tema = p.tema_id
        WHERE {" AND ".join(clauses)}
        ORDER BY t.nombre;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, tuple(params))
            return [str(r["nombre"]).strip() for r in cur.fetchall() if r.get("nombre")]

    def fetch_materia(self, materia_id: int) -> Optional[Dict[str, Any]]:
        query = """
        SELECT id_materia, codigo, nombre, cantidad_preguntas, factor_ponderacion, area_examen
        FROM catalogo_materias
        WHERE id_materia = %s AND activo;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, (int(materia_id),))
            row = cur.fetchone()
            return dict(row) if row else None

    def fetch_justificacion(self, pregunta_id: int) -> Optional[str]:
        """Solo la justificación pedagógica (para tutor); no altera scoring."""
        query = """
        SELECT justificacion
        FROM banco_preguntas
        WHERE id_pregunta = %s;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, (int(pregunta_id),))
            row = cur.fetchone()
            if not row:
                return None
            text = str(row.get("justificacion") or "").strip()
            return text or None

    def upsert_tema(
        self,
        *,
        materia_id: int,
        nombre: str,
        origen_contenido: str = "pdf",
        descripcion: Optional[str] = None,
        cursor: Any = None,
    ) -> int:
        nombre_limpio = normalize_tema_nombre(nombre)
        slug = slugify_tema(nombre_limpio)
        query = """
        INSERT INTO temas_estudio (materia_id, nombre, slug, origen_contenido, descripcion)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (materia_id, slug) DO UPDATE
            SET nombre = EXCLUDED.nombre,
                origen_contenido = COALESCE(EXCLUDED.origen_contenido, temas_estudio.origen_contenido),
                descripcion = COALESCE(EXCLUDED.descripcion, temas_estudio.descripcion),
                activo = TRUE,
                actualizado_en = NOW()
        RETURNING id_tema;
        """
        params = (
            int(materia_id),
            nombre_limpio[:200],
            slug,
            (origen_contenido or "pdf")[:40],
            descripcion,
        )
        if cursor is not None:
            cursor.execute(query, params)
            return int(cursor.fetchone()["id_tema"])

        with self._connection.get_cursor() as cur:
            cur.execute(query, params)
            return int(cur.fetchone()["id_tema"])

    def insert_pregunta(
        self,
        *,
        tema_id: int,
        enunciado: str,
        alternativas: Dict[str, str],
        alternativa_correcta: str,
        justificacion: str,
        fuente: Optional[str] = None,
        anio_referencia: Optional[int] = None,
        nombre_archivo_fuente: Optional[str] = None,
        propietario_usuario_id: Optional[int] = None,
        cursor: Any = None,
    ) -> Optional[int]:
        """
        Inserta ítem. Devuelve id_pregunta o None si ya existía
        (mismo hash para el mismo propietario / oficial).
        """
        alt = {k: str(alternativas[k]).strip() for k in ("A", "B", "C", "D", "E")}
        correcta = alternativa_correcta.strip().upper()
        digest = hash_contenido_pregunta(enunciado, alt, correcta)
        archivo = (nombre_archivo_fuente or "").strip()[:255] or None
        owner = int(propietario_usuario_id) if propietario_usuario_id is not None else None
        owner_key = int(owner) if owner is not None else 0

        # Dedup por dueño sin depender de ON CONFLICT con expresión (portabilidad).
        exists_q = """
        SELECT id_pregunta
        FROM banco_preguntas
        WHERE hash_contenido = %s
          AND COALESCE(propietario_usuario_id, 0) = %s
        LIMIT 1;
        """
        insert_q = """
        INSERT INTO banco_preguntas (
            tema_id, enunciado, alternativas, alternativa_correcta,
            justificacion, fuente, anio_referencia, hash_contenido,
            nombre_archivo_fuente, propietario_usuario_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id_pregunta;
        """
        params = (
            int(tema_id),
            enunciado.strip(),
            Json(alt),
            correcta,
            justificacion.strip(),
            (fuente[:120] if fuente else None),
            anio_referencia,
            digest,
            archivo,
            owner,
        )

        def _run(cur: Any) -> Optional[int]:
            cur.execute(exists_q, (digest, owner_key))
            if cur.fetchone():
                return None
            cur.execute(insert_q, params)
            row = cur.fetchone()
            return int(row["id_pregunta"]) if row else None

        if cursor is not None:
            return _run(cursor)

        with self._connection.get_cursor() as cur:
            return _run(cur)

    def persist_items_transactional(
        self,
        *,
        items: Sequence[Dict[str, Any]],
        materia_id: Optional[int] = None,
        materia_lookup: Optional[Dict[str, int]] = None,
        origen_contenido: str = "pdf",
        fuente: Optional[str] = "openrouter",
        anio_referencia: Optional[int] = None,
        nombre_archivo_fuente: Optional[str] = None,
        propietario_usuario_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Una sola transacción: resuelve materia por ítem → upsert tema → insert.

        Cada ítem puede traer ``materia_nombre`` (auto-clasificación).
        ``materia_id`` actúa solo como fallback si el LLM no clasifica o inventa.
        ``materia_lookup``: dict precargado nombre_norm → id (evita N+1).
        """
        insertadas: List[int] = []
        duplicadas = 0
        # clave: f"{materia_id}:{slug_tema}" → id_tema
        temas_tocados: Dict[str, int] = {}
        por_materia: Dict[int, int] = {}
        fallback_usados = 0
        sin_materia = 0
        archivo = (nombre_archivo_fuente or "").strip()[:255] or None
        owner = int(propietario_usuario_id) if propietario_usuario_id is not None else None

        lookup = materia_lookup
        materias_rows = self.fetch_materias()
        if lookup is None:
            lookup = build_materia_lookup(materias_rows)
        fallback = int(materia_id) if materia_id is not None else None
        if fallback is None:
            fallback = resolve_materia_id(MATERIA_FALLBACK_NOMBRE, lookup)

        id_to_nombre = {
            int(m["id_materia"]): str(m["nombre"]) for m in materias_rows
        }

        with self._connection.get_cursor() as cur:
            for item in items:
                mid = resolve_materia_id(
                    str(item.get("materia_nombre") or ""),
                    lookup,
                    fallback_id=None,
                )
                if mid is None:
                    mid = fallback
                    fallback_usados += 1
                    if mid is None:
                        sin_materia += 1
                        continue

                tema_nombre = normalize_tema_nombre(str(item["tema_especifico"]))
                tema_key = f"{mid}:{slugify_tema(tema_nombre)}"
                if tema_key not in temas_tocados:
                    mat_label = id_to_nombre.get(int(mid), str(mid))
                    tema_id = self.upsert_tema(
                        materia_id=int(mid),
                        nombre=tema_nombre,
                        origen_contenido=origen_contenido,
                        descripcion=(
                            f"Extraído automáticamente · materia={mat_label}"
                        ),
                        cursor=cur,
                    )
                    temas_tocados[tema_key] = tema_id
                else:
                    tema_id = temas_tocados[tema_key]

                new_id = self.insert_pregunta(
                    tema_id=tema_id,
                    enunciado=str(item["enunciado"]),
                    alternativas=dict(item["alternativas"]),
                    alternativa_correcta=str(item["alternativa_correcta"]),
                    justificacion=str(item["justificacion"]),
                    fuente=fuente,
                    anio_referencia=anio_referencia,
                    nombre_archivo_fuente=archivo,
                    propietario_usuario_id=owner,
                    cursor=cur,
                )
                if new_id is None:
                    duplicadas += 1
                else:
                    insertadas.append(new_id)
                    por_materia[int(mid)] = por_materia.get(int(mid), 0) + 1

        return {
            "materia_id": fallback,
            "materias_tocadas": {
                id_to_nombre.get(mid, str(mid)): n for mid, n in por_materia.items()
            },
            "n_materias": len(por_materia),
            "temas_upserted": len(temas_tocados),
            "temas": {k: v for k, v in temas_tocados.items()},
            "preguntas_insertadas": insertadas,
            "n_insertadas": len(insertadas),
            "n_duplicadas": duplicadas,
            "n_fallback_materia": fallback_usados,
            "n_sin_materia": sin_materia,
            "nombre_archivo_fuente": archivo,
            "propietario_usuario_id": owner,
        }


banco_repository = BancoRepository()
