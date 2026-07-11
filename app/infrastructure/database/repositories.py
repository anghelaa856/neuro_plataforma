"""CRUD sobre la tabla memoria_activa (scoped por usuario)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.infrastructure.database.connection import DatabaseConnection, db_connection
from app.infrastructure.database.schema import ensure_schema as ensure_schema_fn


class MemoryCardRepository:
    """Operaciones de lectura/escritura sobre Memoria Activa de un usuario."""

    def __init__(self, connection: Optional[DatabaseConnection] = None) -> None:
        self._connection = connection or db_connection

    def ensure_schema(self) -> None:
        ensure_schema_fn(self._connection)

    def insert_memory_card(
        self,
        area: str,
        tema: str,
        pregunta: str,
        respuesta_referencia: str,
        usuario_id: int,
        respuesta_estudiante: Optional[str] = None,
        nota_ia: Optional[float] = None,
        auditoria_estado: Optional[str] = None,
        auditoria_tiempo_ms: Optional[int] = None,
        intervalo_recomendado_dias: Optional[int] = None,
        plan_estudio: Optional[str] = None,
        tipo_pregunta: Optional[str] = None,
        modo_simulacro: Optional[bool] = None,
        origen_contenido: Optional[str] = None,
        opciones_respuesta: Optional[Any] = None,
        indice_opcion_correcta: Optional[int] = None,
        opcion_estudiante_indice: Optional[int] = None,
        repetitions_count: Optional[int] = None,
        easiness_factor: Optional[float] = None,
        modo_tarjeta: Optional[str] = None,
        nivel_dificultad: Optional[int] = None,
    ) -> int:
        """Inserta tarjeta del usuario y devuelve su ID."""
        if usuario_id is None:
            raise ValueError("usuario_id es obligatorio.")

        query = """
        INSERT INTO memoria_activa (
            usuario_id,
            area,
            tema,
            pregunta,
            respuesta_referencia,
            respuesta_estudiante,
            nota_ia,
            auditoria_estado,
            auditoria_tiempo_ms,
            intervalo_recomendado_dias,
            plan_estudio,
            tipo_pregunta,
            modo_simulacro,
            origen_contenido,
            opciones_respuesta,
            indice_opcion_correcta,
            opcion_estudiante_indice,
            repetitions_count,
            easiness_factor,
            modo_tarjeta,
            nivel_dificultad
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id_tarjeta;
        """
        nivel = 1
        if nivel_dificultad is not None:
            try:
                nivel = max(1, min(3, int(nivel_dificultad)))
            except (TypeError, ValueError):
                nivel = 1
        params = (
            int(usuario_id),
            area,
            tema,
            pregunta,
            respuesta_referencia,
            respuesta_estudiante,
            nota_ia,
            auditoria_estado,
            auditoria_tiempo_ms,
            intervalo_recomendado_dias,
            plan_estudio,
            tipo_pregunta,
            modo_simulacro,
            origen_contenido,
            opciones_respuesta,
            indice_opcion_correcta,
            opcion_estudiante_indice,
            repetitions_count,
            easiness_factor,
            (modo_tarjeta or "concepto").strip().lower() or "concepto",
            nivel,
        )

        with self._connection.get_cursor() as cur:
            cur.execute(query, params)
            result = cur.fetchone()
            if not result:
                raise RuntimeError("No se pudo obtener el id_tarjeta luego de insertar.")
            return int(result["id_tarjeta"])

    def fetch_memory_cards(self, usuario_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """Recupera tarjetas recientes del usuario."""
        query = """
        SELECT
            id_tarjeta,
            usuario_id,
            area,
            tema,
            pregunta,
            respuesta_referencia,
            respuesta_estudiante,
            nota_ia,
            auditoria_estado,
            auditoria_tiempo_ms,
            intervalo_recomendado_dias,
            plan_estudio,
            tipo_pregunta,
            modo_simulacro,
            origen_contenido,
            opciones_respuesta,
            indice_opcion_correcta,
            opcion_estudiante_indice,
            repetitions_count,
            easiness_factor,
            modo_tarjeta,
            nivel_dificultad,
            creado_en
        FROM memoria_activa
        WHERE usuario_id = %s
        ORDER BY id_tarjeta DESC
        LIMIT %s;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, (int(usuario_id), limit))
            return [dict(row) for row in cur.fetchall()]

    def fetch_progress_dashboard(self, usuario_id: int, due_limit: int = 20) -> Dict[str, Any]:
        """Resumen de progreso solo del usuario autenticado."""
        uid = int(usuario_id)
        mastered_query = """
        SELECT COUNT(*) AS temas_dominados
        FROM (
            SELECT COALESCE(NULLIF(TRIM(tema), ''), pregunta) AS tema_key
            FROM memoria_activa
            WHERE usuario_id = %s
            GROUP BY COALESCE(NULLIF(TRIM(tema), ''), pregunta)
            HAVING AVG(COALESCE(nota_ia, 0)) >= 4.0
        ) t;
        """
        due_query = """
        SELECT
            id_tarjeta,
            area,
            tema,
            pregunta,
            creado_en,
            COALESCE(intervalo_recomendado_dias, 1) AS intervalo_recomendado_dias,
            (DATE(creado_en) + COALESCE(intervalo_recomendado_dias, 1)) AS fecha_repaso
        FROM memoria_activa
        WHERE usuario_id = %s
          AND (DATE(creado_en) + COALESCE(intervalo_recomendado_dias, 1)) <= CURRENT_DATE
        ORDER BY fecha_repaso ASC, id_tarjeta DESC
        LIMIT %s;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(mastered_query, (uid,))
            mastered_row = cur.fetchone() or {"temas_dominados": 0}
            cur.execute(due_query, (uid, due_limit))
            due_rows = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """
                SELECT
                    COALESCE(plan_estudio, 'Sin Plan') AS plan,
                    COUNT(*) AS total
                FROM memoria_activa
                WHERE usuario_id = %s
                GROUP BY COALESCE(plan_estudio, 'Sin Plan')
                ORDER BY total DESC;
                """,
                (uid,),
            )
            by_plan_rows = [dict(row) for row in cur.fetchall()]

        return {
            "temas_dominados": int(mastered_row.get("temas_dominados", 0)),
            "repasos_hoy": due_rows,
            "total_repasos_hoy": len(due_rows),
            "por_plan": by_plan_rows,
        }

    def fetch_topic_latest_state(self, usuario_id: int, tema: str) -> Dict[str, Any]:
        """Estado reciente de un tema para el usuario."""
        query = """
        SELECT
            id_tarjeta,
            COALESCE(intervalo_recomendado_dias, 1) AS intervalo_recomendado_dias,
            COALESCE(repetitions_count, 0) AS repetitions_count,
            COALESCE(easiness_factor, 2.5) AS easiness_factor
        FROM memoria_activa
        WHERE usuario_id = %s
          AND TRIM(COALESCE(tema, '')) = TRIM(%s)
        ORDER BY id_tarjeta DESC
        LIMIT 1;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, (int(usuario_id), tema))
            row = cur.fetchone()
            if not row:
                return {
                    "is_new_topic": True,
                    "intervalo_recomendado_dias": 1,
                    "repetitions_count": 0,
                    "easiness_factor": 2.5,
                }

            payload = dict(row)
            payload["is_new_topic"] = False
            return payload

    def fetch_due_study_cards(self, usuario_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        """Cola de estudio del usuario: pendientes + vencidas."""
        query = """
        SELECT *
        FROM (
            SELECT DISTINCT ON (TRIM(COALESCE(tema, '')), TRIM(COALESCE(pregunta, '')))
                id_tarjeta,
                usuario_id,
                area,
                tema,
                pregunta,
                respuesta_referencia,
                respuesta_estudiante,
                COALESCE(modo_tarjeta, 'concepto') AS modo_tarjeta,
                COALESCE(nivel_dificultad, 1) AS nivel_dificultad,
                COALESCE(repetitions_count, 0) AS repetitions_count,
                COALESCE(easiness_factor, 2.5) AS easiness_factor,
                COALESCE(intervalo_recomendado_dias, 1) AS intervalo_recomendado_dias,
                plan_estudio,
                tipo_pregunta,
                origen_contenido,
                auditoria_estado,
                creado_en,
                (DATE(creado_en) + COALESCE(intervalo_recomendado_dias, 1)) AS fecha_repaso
            FROM memoria_activa
            WHERE usuario_id = %s
              AND (
                respuesta_estudiante IS NULL
                OR TRIM(COALESCE(auditoria_estado, '')) = 'Pendiente'
                OR (DATE(creado_en) + COALESCE(intervalo_recomendado_dias, 1)) <= CURRENT_DATE
              )
            ORDER BY
                TRIM(COALESCE(tema, '')),
                TRIM(COALESCE(pregunta, '')),
                id_tarjeta DESC
        ) q
        ORDER BY
            CASE
                WHEN q.respuesta_estudiante IS NULL THEN 0
                WHEN TRIM(COALESCE(q.auditoria_estado, '')) = 'Pendiente' THEN 0
                ELSE 1
            END,
            q.fecha_repaso ASC NULLS FIRST,
            q.id_tarjeta DESC
        LIMIT %s;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, (int(usuario_id), limit))
            return [dict(row) for row in cur.fetchall()]


memory_card_repository = MemoryCardRepository()
