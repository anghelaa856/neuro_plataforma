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
    """Slug estable para UNIQUE (materia_id, slug)."""
    text = unicodedata.normalize("NFKD", (nombre or "").strip())
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("M"))
    text = text.lower()
    text = re.sub(r"[^a-z0-9áéíóúüñ]+", "-", text, flags=re.IGNORECASE)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    if not text:
        text = "tema"
    return text[:max_len]


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

    def upsert_tema(
        self,
        *,
        materia_id: int,
        nombre: str,
        origen_contenido: str = "pdf",
        descripcion: Optional[str] = None,
        cursor: Any = None,
    ) -> int:
        slug = slugify_tema(nombre)
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
            nombre.strip()[:200],
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
        cursor: Any = None,
    ) -> Optional[int]:
        """
        Inserta ítem. Devuelve id_pregunta o None si ya existía (mismo hash).
        """
        alt = {k: str(alternativas[k]).strip() for k in ("A", "B", "C", "D", "E")}
        correcta = alternativa_correcta.strip().upper()
        digest = hash_contenido_pregunta(enunciado, alt, correcta)
        query = """
        INSERT INTO banco_preguntas (
            tema_id, enunciado, alternativas, alternativa_correcta,
            justificacion, fuente, anio_referencia, hash_contenido
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hash_contenido) DO NOTHING
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
        )
        if cursor is not None:
            cursor.execute(query, params)
            row = cursor.fetchone()
            return int(row["id_pregunta"]) if row else None

        with self._connection.get_cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return int(row["id_pregunta"]) if row else None

    def persist_items_transactional(
        self,
        *,
        materia_id: int,
        items: Sequence[Dict[str, Any]],
        origen_contenido: str = "pdf",
        fuente: Optional[str] = "openrouter",
        anio_referencia: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Una sola transacción: upsert temas + insert preguntas.
        ``items``: dicts con tema_especifico, enunciado, alternativas,
        alternativa_correcta, justificacion.
        """
        insertadas: List[int] = []
        duplicadas = 0
        temas_tocados: Dict[str, int] = {}

        with self._connection.get_cursor() as cur:
            for item in items:
                tema_nombre = str(item["tema_especifico"]).strip()
                if tema_nombre not in temas_tocados:
                    tema_id = self.upsert_tema(
                        materia_id=materia_id,
                        nombre=tema_nombre,
                        origen_contenido=origen_contenido,
                        descripcion=f"Extraído automáticamente · materia_id={materia_id}",
                        cursor=cur,
                    )
                    temas_tocados[tema_nombre] = tema_id
                else:
                    tema_id = temas_tocados[tema_nombre]

                new_id = self.insert_pregunta(
                    tema_id=tema_id,
                    enunciado=str(item["enunciado"]),
                    alternativas=dict(item["alternativas"]),
                    alternativa_correcta=str(item["alternativa_correcta"]),
                    justificacion=str(item["justificacion"]),
                    fuente=fuente,
                    anio_referencia=anio_referencia,
                    cursor=cur,
                )
                if new_id is None:
                    duplicadas += 1
                else:
                    insertadas.append(new_id)

        return {
            "materia_id": int(materia_id),
            "temas_upserted": len(temas_tocados),
            "temas": dict(temas_tocados),
            "preguntas_insertadas": insertadas,
            "n_insertadas": len(insertadas),
            "n_duplicadas": duplicadas,
        }


banco_repository = BancoRepository()
