"""Creación y migración del esquema de Memoria Activa."""

from __future__ import annotations

from app.infrastructure.database.connection import DatabaseConnection, db_connection

CREATE_MEMORIA_ACTIVA = """
CREATE TABLE IF NOT EXISTS memoria_activa (
    id_tarjeta BIGSERIAL PRIMARY KEY,
    area VARCHAR(120),
    tema VARCHAR(200),
    pregunta TEXT,
    respuesta_referencia TEXT,
    respuesta_estudiante TEXT,
    nota_ia NUMERIC(4, 2),
    auditoria_estado VARCHAR(40),
    auditoria_tiempo_ms INTEGER,
    intervalo_recomendado_dias INTEGER,
    plan_estudio VARCHAR(80),
    tipo_pregunta VARCHAR(20),
    modo_simulacro BOOLEAN DEFAULT FALSE,
    origen_contenido VARCHAR(30),
    opciones_respuesta JSONB,
    indice_opcion_correcta INTEGER,
    opcion_estudiante_indice INTEGER,
    repetitions_count INTEGER,
    easiness_factor NUMERIC(6, 3),
    modo_tarjeta VARCHAR(20) DEFAULT 'concepto',
    nivel_dificultad INTEGER DEFAULT 1,
    creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

MIGRATION_STATEMENTS = [
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS id_tarjeta BIGSERIAL;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS area VARCHAR(120);",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS tema VARCHAR(200);",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS pregunta TEXT;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS respuesta_referencia TEXT;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS respuesta_estudiante TEXT;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS nota_ia NUMERIC(4, 2);",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS auditoria_estado VARCHAR(40);",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS auditoria_tiempo_ms INTEGER;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS intervalo_recomendado_dias INTEGER;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS plan_estudio VARCHAR(80);",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS tipo_pregunta VARCHAR(20);",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS modo_simulacro BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS origen_contenido VARCHAR(30);",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS opciones_respuesta JSONB;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS indice_opcion_correcta INTEGER;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS opcion_estudiante_indice INTEGER;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS repetitions_count INTEGER;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS easiness_factor NUMERIC(6, 3);",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS modo_tarjeta VARCHAR(20) DEFAULT 'concepto';",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS nivel_dificultad INTEGER DEFAULT 1;",
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP;",
]


def ensure_schema(connection: DatabaseConnection | None = None) -> None:
    """Crea/migra la tabla memoria_activa de forma compatible con esquemas previos."""
    conn = connection or db_connection
    with conn.get_cursor() as cur:
        cur.execute(CREATE_MEMORIA_ACTIVA)
        for stmt in MIGRATION_STATEMENTS:
            cur.execute(stmt)
