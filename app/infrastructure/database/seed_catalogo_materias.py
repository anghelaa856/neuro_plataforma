"""
Seed oficial: catalogo_materias — Área Biomédicas (Tabla 4, Reglamento UNA).

Idempotente vía UNIQUE (area_examen, codigo). No muta filas existentes
(el catálogo es inmutable una vez sembrado).

Uso:
    python -m app.infrastructure.database.seed_catalogo_materias
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Sequence, Tuple

from app.infrastructure.database.connection import DatabaseConnection, db_connection
from app.infrastructure.database.schema import ensure_schema

logger = logging.getLogger(__name__)

AREA_EXAMEN = "BIOMEDICAS"

# Tabla 4 — Área Biomédicas (prospecto oficial).
# Campos lógicos: nombre_materia → columna `nombre` del DDL validado.
MATERIAS_BIOMEDICAS: List[Dict[str, Any]] = [
    {"codigo": 1, "nombre_materia": "Aritmética", "cantidad_preguntas": 3, "factor_ponderacion": 3.331},
    {"codigo": 2, "nombre_materia": "Álgebra", "cantidad_preguntas": 3, "factor_ponderacion": 3.202},
    {"codigo": 3, "nombre_materia": "Geometría", "cantidad_preguntas": 3, "factor_ponderacion": 3.301},
    {"codigo": 4, "nombre_materia": "Trigonometría", "cantidad_preguntas": 3, "factor_ponderacion": 3.404},
    {"codigo": 5, "nombre_materia": "Física", "cantidad_preguntas": 3, "factor_ponderacion": 5.505},
    {"codigo": 6, "nombre_materia": "Química", "cantidad_preguntas": 5, "factor_ponderacion": 6.623},
    {"codigo": 7, "nombre_materia": "Biología y Anatomía", "cantidad_preguntas": 6, "factor_ponderacion": 7.816},
    {"codigo": 8, "nombre_materia": "Psicología y Filosofía", "cantidad_preguntas": 4, "factor_ponderacion": 4.006},
    {"codigo": 9, "nombre_materia": "Geografía", "cantidad_preguntas": 2, "factor_ponderacion": 2.800},
    {"codigo": 10, "nombre_materia": "Historia", "cantidad_preguntas": 2, "factor_ponderacion": 3.302},
    {"codigo": 11, "nombre_materia": "Educación Cívica", "cantidad_preguntas": 2, "factor_ponderacion": 3.571},
    {"codigo": 12, "nombre_materia": "Economía", "cantidad_preguntas": 2, "factor_ponderacion": 3.406},
    {"codigo": 13, "nombre_materia": "Comunicación", "cantidad_preguntas": 4, "factor_ponderacion": 3.302},
    {"codigo": 14, "nombre_materia": "Literatura", "cantidad_preguntas": 2, "factor_ponderacion": 2.805},
    {"codigo": 15, "nombre_materia": "Razonamiento Matemático", "cantidad_preguntas": 6, "factor_ponderacion": 7.201},
    {"codigo": 16, "nombre_materia": "Razonamiento Verbal", "cantidad_preguntas": 6, "factor_ponderacion": 7.201},
    {"codigo": 17, "nombre_materia": "Inglés", "cantidad_preguntas": 2, "factor_ponderacion": 4.087},
    {"codigo": 18, "nombre_materia": "Quechua y aimara", "cantidad_preguntas": 2, "factor_ponderacion": 4.087},
]

INSERT_SQL = """
INSERT INTO catalogo_materias
    (codigo, nombre, cantidad_preguntas, factor_ponderacion, area_examen)
VALUES
    (%s, %s, %s, %s, %s)
ON CONFLICT (area_examen, codigo) DO NOTHING;
"""


def _validate_blueprint(materias: Sequence[Dict[str, Any]]) -> None:
    if len(materias) != 18:
        raise ValueError(f"Se esperaban 18 materias Biomédicas; hay {len(materias)}.")
    total_preguntas = sum(int(m["cantidad_preguntas"]) for m in materias)
    if total_preguntas != 60:
        raise ValueError(
            f"Σ cantidad_preguntas debe ser 60; se obtuvo {total_preguntas}."
        )
    techo = sum(
        10 * int(m["cantidad_preguntas"]) * float(m["factor_ponderacion"])
        for m in materias
    )
    # Tolerancia de redondeo del reglamento (~3000)
    if abs(techo - 3000.0) > 2.0:
        raise ValueError(
            f"Techo ponderado esperado ~3000; se obtuvo {techo:.3f}."
        )


def seed_catalogo_materias(
    connection: DatabaseConnection | None = None,
    *,
    ensure_schema_first: bool = True,
) -> Dict[str, Any]:
    """
    Inserta las 18 materias del Área Biomédicas.
    Devuelve conteos de insertadas vs ya existentes.
    """
    _validate_blueprint(MATERIAS_BIOMEDICAS)
    conn = connection or db_connection

    if ensure_schema_first:
        ensure_schema(conn)

    params: List[Tuple[Any, ...]] = [
        (
            int(m["codigo"]),
            str(m["nombre_materia"]),
            int(m["cantidad_preguntas"]),
            float(m["factor_ponderacion"]),
            AREA_EXAMEN,
        )
        for m in MATERIAS_BIOMEDICAS
    ]

    inserted = 0
    with conn.get_cursor() as cur:
        for row in params:
            cur.execute(INSERT_SQL, row)
            inserted += cur.rowcount

        cur.execute(
            """
            SELECT codigo, nombre AS nombre_materia, cantidad_preguntas, factor_ponderacion
            FROM catalogo_materias
            WHERE area_examen = %s AND activo
            ORDER BY codigo;
            """,
            (AREA_EXAMEN,),
        )
        rows = [dict(r) for r in cur.fetchall()]

    skipped = len(MATERIAS_BIOMEDICAS) - inserted
    logger.info(
        "Seed catalogo_materias BIOMEDICAS: insertadas=%s ya_existentes=%s total=%s",
        inserted,
        skipped,
        len(rows),
    )
    return {
        "area_examen": AREA_EXAMEN,
        "insertadas": inserted,
        "ya_existentes": skipped,
        "total_activas": len(rows),
        "materias": rows,
        "techo_ponderado_teorico": round(
            sum(
                10 * int(m["cantidad_preguntas"]) * float(m["factor_ponderacion"])
                for m in MATERIAS_BIOMEDICAS
            ),
            3,
        ),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.database.db_manager import db_manager

    db_manager.connect()
    try:
        result = seed_catalogo_materias()
        print(
            f"OK — insertadas={result['insertadas']} "
            f"ya_existentes={result['ya_existentes']} "
            f"total={result['total_activas']} "
            f"techo≈{result['techo_ponderado_teorico']}"
        )
        for m in result["materias"]:
            print(
                f"  [{m['codigo']:02d}] {m['nombre_materia']}: "
                f"{m['cantidad_preguntas']} Q × factor {m['factor_ponderacion']}"
            )
    finally:
        db_manager.close()


if __name__ == "__main__":
    main()
