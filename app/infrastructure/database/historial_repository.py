"""
Ledger UNA: historial_intentos (append-only) + cierre de sesiones_simulacro.

La UI / TutorEngine no deben escribir SQL; esta capa es el único punto de
INSERT/UPDATE del ciclo de feedback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.infrastructure.database.connection import DatabaseConnection, db_connection


@dataclass(frozen=True)
class IntentoLedger:
    """Fila lista para INSERT en historial_intentos."""

    usuario_id: int
    pregunta_id: int
    sesion_id: Optional[int]
    orden_en_sesion: Optional[int]
    tiempo_respuesta_ms: int
    alternativa_marcada: Optional[str]
    es_correcta: Optional[bool]
    puntaje_obtenido: int
    factor_ponderacion_aplicado: Optional[float]
    puntaje_ponderado: Optional[float]


@dataclass(frozen=True)
class CierreSesionSimulacro:
    """Agregados materializados al cerrar un simulacro oficial."""

    respuestas_correctas: int
    respuestas_incorrectas: int
    respuestas_en_blanco: int
    puntaje_bruto: float
    puntaje_ponderado: float
    tiempo_total_ms: int


class HistorialRepository:
    """Persistencia del ciclo de feedback (práctica libre + simulacro)."""

    def __init__(self, connection: Optional[DatabaseConnection] = None) -> None:
        self._connection = connection or db_connection

    def insert_intentos(self, intentos: Sequence[IntentoLedger]) -> List[int]:
        """INSERT-only al ledger. Devuelve los id_intento generados."""
        if not intentos:
            return []

        query = """
        INSERT INTO historial_intentos (
            usuario_id,
            pregunta_id,
            sesion_id,
            orden_en_sesion,
            tiempo_respuesta_ms,
            alternativa_marcada,
            es_correcta,
            puntaje_obtenido,
            factor_ponderacion_aplicado,
            puntaje_ponderado
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING id_intento;
        """
        ids: List[int] = []
        with self._connection.get_cursor() as cur:
            for item in intentos:
                marcada = item.alternativa_marcada
                if marcada is not None:
                    marcada = str(marcada).strip().upper() or None
                cur.execute(
                    query,
                    (
                        int(item.usuario_id),
                        int(item.pregunta_id),
                        int(item.sesion_id) if item.sesion_id is not None else None,
                        int(item.orden_en_sesion)
                        if item.orden_en_sesion is not None
                        else None,
                        max(0, int(item.tiempo_respuesta_ms)),
                        marcada,
                        item.es_correcta,
                        int(item.puntaje_obtenido),
                        float(item.factor_ponderacion_aplicado)
                        if item.factor_ponderacion_aplicado is not None
                        else None,
                        float(item.puntaje_ponderado)
                        if item.puntaje_ponderado is not None
                        else None,
                    ),
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("INSERT historial_intentos no devolvió id_intento.")
                ids.append(int(row["id_intento"]))
        return ids

    def finalizar_sesion_simulacro(
        self,
        *,
        id_sesion: int,
        usuario_id: int,
        cierre: CierreSesionSimulacro,
    ) -> Dict[str, Any]:
        """Marca sesiones_simulacro como finalizada y guarda agregados."""
        query = """
        UPDATE sesiones_simulacro
        SET
            estado = 'finalizada',
            finalizada_en = NOW(),
            respuestas_correctas = %s,
            respuestas_incorrectas = %s,
            respuestas_en_blanco = %s,
            puntaje_bruto = %s,
            puntaje_ponderado = %s,
            tiempo_total_ms = %s
        WHERE id_sesion = %s
          AND usuario_id = %s
          AND estado = 'en_curso'
        RETURNING id_sesion, estado, finalizada_en, puntaje_ponderado, puntaje_bruto;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(
                query,
                (
                    int(cierre.respuestas_correctas),
                    int(cierre.respuestas_incorrectas),
                    int(cierre.respuestas_en_blanco),
                    float(cierre.puntaje_bruto),
                    float(cierre.puntaje_ponderado),
                    max(0, int(cierre.tiempo_total_ms)),
                    int(id_sesion),
                    int(usuario_id),
                ),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError(
                    f"No se pudo finalizar sesión {id_sesion}: "
                    "inexistente, de otro usuario o ya cerrada."
                )
            return dict(row)

    def persistir_cierre_simulacro(
        self,
        *,
        intentos: Sequence[IntentoLedger],
        id_sesion: int,
        usuario_id: int,
        cierre: CierreSesionSimulacro,
    ) -> Dict[str, Any]:
        """
        Transacción única: INSERT ledger + UPDATE sesiones_simulacro.
        Evita simulacro cerrado sin intentos (o al revés).
        """
        if not intentos:
            raise ValueError("No hay intentos para persistir el cierre del simulacro.")

        insert_q = """
        INSERT INTO historial_intentos (
            usuario_id, pregunta_id, sesion_id, orden_en_sesion,
            tiempo_respuesta_ms, alternativa_marcada, es_correcta,
            puntaje_obtenido, factor_ponderacion_aplicado, puntaje_ponderado
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id_intento;
        """
        update_q = """
        UPDATE sesiones_simulacro
        SET
            estado = 'finalizada',
            finalizada_en = NOW(),
            respuestas_correctas = %s,
            respuestas_incorrectas = %s,
            respuestas_en_blanco = %s,
            puntaje_bruto = %s,
            puntaje_ponderado = %s,
            tiempo_total_ms = %s
        WHERE id_sesion = %s
          AND usuario_id = %s
          AND estado = 'en_curso'
        RETURNING id_sesion, estado, finalizada_en, puntaje_ponderado, puntaje_bruto;
        """
        ids: List[int] = []
        with self._connection.get_cursor() as cur:
            for item in intentos:
                marcada = item.alternativa_marcada
                if marcada is not None:
                    marcada = str(marcada).strip().upper() or None
                cur.execute(
                    insert_q,
                    (
                        int(item.usuario_id),
                        int(item.pregunta_id),
                        int(item.sesion_id) if item.sesion_id is not None else None,
                        int(item.orden_en_sesion)
                        if item.orden_en_sesion is not None
                        else None,
                        max(0, int(item.tiempo_respuesta_ms)),
                        marcada,
                        item.es_correcta,
                        int(item.puntaje_obtenido),
                        float(item.factor_ponderacion_aplicado)
                        if item.factor_ponderacion_aplicado is not None
                        else None,
                        float(item.puntaje_ponderado)
                        if item.puntaje_ponderado is not None
                        else None,
                    ),
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("INSERT historial_intentos falló en cierre de simulacro.")
                ids.append(int(row["id_intento"]))

            cur.execute(
                update_q,
                (
                    int(cierre.respuestas_correctas),
                    int(cierre.respuestas_incorrectas),
                    int(cierre.respuestas_en_blanco),
                    float(cierre.puntaje_bruto),
                    float(cierre.puntaje_ponderado),
                    max(0, int(cierre.tiempo_total_ms)),
                    int(id_sesion),
                    int(usuario_id),
                ),
            )
            sesion_row = cur.fetchone()
            if not sesion_row:
                raise RuntimeError(
                    f"Intentos insertados pero la sesión {id_sesion} no se pudo cerrar "
                    "(¿ya finalizada?). Se hará rollback."
                )

        return {
            "ids_intento": ids,
            "n_insertados": len(ids),
            "sesion": dict(sesion_row),
        }

    # ---------------------------------------------------------- Analytics alumno
    def fetch_kpis_alumno(self, usuario_id: int) -> Dict[str, Any]:
        """
        KPIs globales del ledger para un usuario (práctica + simulacro).
        Strictamente filtrado por usuario_id.
        """
        query = """
        SELECT
            COUNT(*)::int AS total_intentos,
            COUNT(*) FILTER (WHERE es_correcta IS TRUE)::int AS correctas,
            COUNT(*) FILTER (WHERE es_correcta IS FALSE)::int AS incorrectas,
            COUNT(*) FILTER (WHERE es_correcta IS NULL)::int AS en_blanco,
            COALESCE(SUM(puntaje_obtenido), 0)::float AS puntaje_bruto_acumulado,
            COALESCE(SUM(puntaje_ponderado), 0)::float AS puntaje_ponderado_acumulado
        FROM historial_intentos
        WHERE usuario_id = %s;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, (int(usuario_id),))
            row = cur.fetchone() or {}

        total = int(row.get("total_intentos") or 0)
        correctas = int(row.get("correctas") or 0)
        incorrectas = int(row.get("incorrectas") or 0)
        en_blanco = int(row.get("en_blanco") or 0)
        decididos = correctas + incorrectas
        precision_pct = (
            round(100.0 * correctas / decididos, 1) if decididos > 0 else 0.0
        )
        # Proyección simplificada pedida por producto (no es el techo oficial 60Q).
        puntaje_una_proyectado = int(correctas * 10 - incorrectas * 2)

        return {
            "usuario_id": int(usuario_id),
            "total_intentos": total,
            "correctas": correctas,
            "incorrectas": incorrectas,
            "en_blanco": en_blanco,
            "precision_pct": precision_pct,
            "puntaje_una_proyectado": puntaje_una_proyectado,
            "puntaje_bruto_acumulado": float(row.get("puntaje_bruto_acumulado") or 0),
            "puntaje_ponderado_acumulado": float(
                row.get("puntaje_ponderado_acumulado") or 0
            ),
        }

    def fetch_rendimiento_por_tema(self, usuario_id: int) -> List[Dict[str, Any]]:
        """
        Aciertos/errores agrupados por materia + tema (JOIN bóveda).
        """
        query = """
        SELECT
            m.id_materia,
            m.codigo AS materia_codigo,
            m.nombre AS materia_nombre,
            t.id_tema,
            t.nombre AS tema_nombre,
            COUNT(*)::int AS n_intentos,
            COUNT(*) FILTER (WHERE h.es_correcta IS TRUE)::int AS correctas,
            COUNT(*) FILTER (WHERE h.es_correcta IS FALSE)::int AS incorrectas,
            COUNT(*) FILTER (WHERE h.es_correcta IS NULL)::int AS en_blanco
        FROM historial_intentos h
        JOIN banco_preguntas p ON p.id_pregunta = h.pregunta_id
        JOIN temas_estudio t ON t.id_tema = p.tema_id
        JOIN catalogo_materias m ON m.id_materia = t.materia_id
        WHERE h.usuario_id = %s
        GROUP BY m.id_materia, m.codigo, m.nombre, t.id_tema, t.nombre
        ORDER BY m.codigo ASC, t.nombre ASC;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, (int(usuario_id),))
            rows = [dict(r) for r in cur.fetchall()]

        out: List[Dict[str, Any]] = []
        for r in rows:
            correctas = int(r.get("correctas") or 0)
            incorrectas = int(r.get("incorrectas") or 0)
            decididos = correctas + incorrectas
            precision = (
                round(100.0 * correctas / decididos, 1) if decididos > 0 else 0.0
            )
            if precision < 40:
                semaforo = "rojo"
            elif precision < 70:
                semaforo = "amarillo"
            else:
                semaforo = "verde"
            out.append(
                {
                    "id_materia": int(r["id_materia"]),
                    "materia_codigo": int(r["materia_codigo"]),
                    "materia_nombre": str(r["materia_nombre"]),
                    "id_tema": int(r["id_tema"]),
                    "tema_nombre": str(r["tema_nombre"]),
                    "n_intentos": int(r.get("n_intentos") or 0),
                    "correctas": correctas,
                    "incorrectas": incorrectas,
                    "en_blanco": int(r.get("en_blanco") or 0),
                    "precision_pct": precision,
                    "semaforo": semaforo,
                }
            )
        return out

    def fetch_precision_ventanas(
        self, usuario_id: int, *, dias: int = 7
    ) -> Dict[str, Any]:
        """
        Precisión de los últimos ``dias`` vs los ``dias`` previos (para delta semanal).
        Blancos no entran en el %.
        """
        ventana = max(1, int(dias))
        query = """
        WITH base AS (
            SELECT
                es_correcta,
                fecha_hora
            FROM historial_intentos
            WHERE usuario_id = %s
              AND es_correcta IS NOT NULL
              AND fecha_hora >= NOW() - (%s || ' days')::interval
        )
        SELECT
            COUNT(*) FILTER (
                WHERE fecha_hora >= NOW() - (%s || ' days')::interval
            )::int AS n_reciente,
            COUNT(*) FILTER (
                WHERE fecha_hora >= NOW() - (%s || ' days')::interval
                  AND es_correcta IS TRUE
            )::int AS ok_reciente,
            COUNT(*) FILTER (
                WHERE fecha_hora < NOW() - (%s || ' days')::interval
            )::int AS n_previa,
            COUNT(*) FILTER (
                WHERE fecha_hora < NOW() - (%s || ' days')::interval
                  AND es_correcta IS TRUE
            )::int AS ok_previa
        FROM base;
        """
        # Ventana total = 2 * dias (reciente + previa).
        with self._connection.get_cursor() as cur:
            cur.execute(
                query,
                (
                    int(usuario_id),
                    str(ventana * 2),
                    str(ventana),
                    str(ventana),
                    str(ventana),
                    str(ventana),
                ),
            )
            row = cur.fetchone() or {}

        n_rec = int(row.get("n_reciente") or 0)
        ok_rec = int(row.get("ok_reciente") or 0)
        n_prev = int(row.get("n_previa") or 0)
        ok_prev = int(row.get("ok_previa") or 0)
        precision_7d = round(100.0 * ok_rec / n_rec, 1) if n_rec > 0 else None
        precision_7d_prev = (
            round(100.0 * ok_prev / n_prev, 1) if n_prev > 0 else None
        )
        return {
            "dias": ventana,
            "n_reciente": n_rec,
            "precision_7d": precision_7d,
            "n_previa": n_prev,
            "precision_7d_prev": precision_7d_prev,
        }


historial_repository = HistorialRepository()
