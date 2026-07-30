"""
Telemetría append-only de tutorías socráticas.

NO es ledger de calificación: no alimenta SRS ni historial_intentos.
Sin FKs estrictas (auditoría no debe fallar por integridad referencial).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.infrastructure.database.connection import DatabaseConnection, db_connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TutorInteraccionInsert:
    """Fila lista para INSERT en tutor_interacciones."""

    usuario_id: Optional[int]
    pregunta_id: int
    modo_origen: str
    mensaje_alumno: str
    respuesta_ia: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    model: Optional[str] = None
    method: Optional[str] = None
    spoilers_bloqueados: bool = False


class TutorInteraccionesRepository:
    """Persistencia de telemetría del tutor socrático."""

    def __init__(self, connection: Optional[DatabaseConnection] = None) -> None:
        self._connection = connection or db_connection

    def guardar_interaccion_tutor(self, datos: TutorInteraccionInsert) -> Optional[int]:
        """
        INSERT append-only. Devuelve id o None si falló (nunca levanta al caller).
        """
        query = """
        INSERT INTO tutor_interacciones (
            usuario_id,
            pregunta_id,
            modo_origen,
            mensaje_alumno,
            respuesta_ia,
            prompt_tokens,
            completion_tokens,
            model,
            method,
            spoilers_bloqueados
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id;
        """
        modo = (datos.modo_origen or "desconocido").strip().lower()[:40] or "desconocido"
        try:
            with self._connection.get_cursor() as cur:
                cur.execute(
                    query,
                    (
                        int(datos.usuario_id) if datos.usuario_id is not None else None,
                        int(datos.pregunta_id),
                        modo,
                        str(datos.mensaje_alumno or ""),
                        str(datos.respuesta_ia or ""),
                        int(datos.prompt_tokens)
                        if datos.prompt_tokens is not None
                        else None,
                        int(datos.completion_tokens)
                        if datos.completion_tokens is not None
                        else None,
                        (datos.model or None),
                        (datos.method or None),
                        bool(datos.spoilers_bloqueados),
                    ),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return int(row["id"] if isinstance(row, dict) else row[0])
        except Exception as exc:
            logger.warning(
                "Telemetría tutor omitida (pregunta_id=%s): %s: %s",
                datos.pregunta_id,
                type(exc).__name__,
                exc,
            )
            return None


tutor_interacciones_repository = TutorInteraccionesRepository()
