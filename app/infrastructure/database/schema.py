"""Creación y migración del esquema (usuarios + Memoria Activa)."""

from __future__ import annotations

import logging

from app.infrastructure.database.connection import DatabaseConnection, db_connection

logger = logging.getLogger(__name__)

CREATE_USUARIOS = """
CREATE TABLE IF NOT EXISTS usuarios (
    id_usuario BIGSERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    nombre VARCHAR(120) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Sin FK inline: la tabla puede existir desde deploys previos sin usuarios.
# La FK se agrega después en migraciones, una vez que usuarios existe.
CREATE_MEMORIA_ACTIVA = """
CREATE TABLE IF NOT EXISTS memoria_activa (
    id_tarjeta BIGSERIAL PRIMARY KEY,
    usuario_id BIGINT,
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
    proxima_revision DATE,
    creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

MIGRATION_STATEMENTS = [
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
    # Vínculo multiusuario (obligatorio)
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS usuario_id BIGINT;",
    # Fecha de próximo repaso (persiste progreso SM-2 entre sesiones)
    "ALTER TABLE memoria_activa ADD COLUMN IF NOT EXISTS proxima_revision DATE;",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'fk_memoria_activa_usuario'
        ) THEN
            ALTER TABLE memoria_activa
                ADD CONSTRAINT fk_memoria_activa_usuario
                FOREIGN KEY (usuario_id) REFERENCES usuarios(id_usuario)
                ON DELETE CASCADE;
        END IF;
    EXCEPTION
        WHEN undefined_table THEN NULL;
        WHEN duplicate_object THEN NULL;
        WHEN others THEN NULL;
    END $$;
    """,
    "CREATE INDEX IF NOT EXISTS idx_memoria_activa_usuario_id ON memoria_activa(usuario_id);",
    "CREATE INDEX IF NOT EXISTS idx_memoria_activa_proxima_revision ON memoria_activa(proxima_revision);",
    "CREATE INDEX IF NOT EXISTS idx_usuarios_email ON usuarios(email);",
    # Tarjetas pendientes/heredadas sin fecha: due hoy
    """
    UPDATE memoria_activa
    SET proxima_revision = CURRENT_DATE
    WHERE proxima_revision IS NULL
      AND (
        respuesta_estudiante IS NULL
        OR TRIM(COALESCE(auditoria_estado, '')) = 'Pendiente'
      );
    """,
]


def _run_sql(connection: DatabaseConnection, sql: str, *, critical: bool = False) -> None:
    """
    Ejecuta DDL en su propia transacción.
    Así un ALTER fallido NO hace rollback del CREATE TABLE usuarios.
    """
    try:
        with connection.get_cursor() as cur:
            cur.execute(sql)
    except Exception as exc:
        if critical:
            raise
        logger.warning("DDL no crítico omitido: %s | sql=%s", exc, " ".join(sql.split())[:120])


def ensure_schema(connection: DatabaseConnection | None = None) -> None:
    """
    Crea/migra:
    1) tabla usuarios
    2) tabla memoria_activa
    3) columna usuario_id + FK/índices
    """
    conn = connection or db_connection

    # 1) Usuarios — crítico para login/registro
    _run_sql(conn, CREATE_USUARIOS, critical=True)
    logger.info("Esquema: tabla usuarios OK")

    # 2) Memoria activa
    _run_sql(conn, CREATE_MEMORIA_ACTIVA, critical=True)
    logger.info("Esquema: tabla memoria_activa OK")

    # 3) Migraciones (incluyen usuario_id)
    for stmt in MIGRATION_STATEMENTS:
        # usuario_id debe aplicarse sí o sí
        critical = "usuario_id" in stmt and "ADD COLUMN" in stmt.upper()
        _run_sql(conn, stmt, critical=critical)

    logger.info("Esquema: migraciones memoria_activa/usuarios aplicadas")
