"""Materializa el DDL del Simulador de Admisión UNA sin tocar memoria_activa."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.infrastructure.database.connection import DatabaseConnection, db_connection

logger = logging.getLogger(__name__)

DDL_FILE = Path(__file__).with_name("ddl_simulador_admision_una.sql")

# Tablas del dominio admisión; memoria_activa / usuarios quedan fuera de este módulo.
ADMISSION_TABLES = (
    "catalogo_materias",
    "temas_estudio",
    "banco_preguntas",
    "sesiones_simulacro",
    "historial_intentos",
)


def _strip_sql_comments(sql: str) -> str:
    """Elimina comentarios de línea (-- ...) respetando strings simples."""
    out: list[str] = []
    for line in sql.splitlines():
        in_single = False
        buf: list[str] = []
        i = 0
        while i < len(line):
            ch = line[i]
            if ch == "'" and not in_single:
                in_single = True
                buf.append(ch)
                i += 1
                continue
            if ch == "'" and in_single:
                # Escape '' dentro de literales
                if i + 1 < len(line) and line[i + 1] == "'":
                    buf.append("''")
                    i += 2
                    continue
                in_single = False
                buf.append(ch)
                i += 1
                continue
            if not in_single and ch == "-" and i + 1 < len(line) and line[i + 1] == "-":
                break
            buf.append(ch)
            i += 1
        out.append("".join(buf))
    return "\n".join(out)


def _split_sql_statements(sql: str) -> list[str]:
    """
    Parte el script en sentencias ejecutables.
    Omite BEGIN/COMMIT exteriores (el pool ya gestiona la transacción).
    """
    cleaned = _strip_sql_comments(sql)
    # Quita envoltorio transaccional del archivo DDL
    cleaned = re.sub(r"^\s*BEGIN\s*;", "", cleaned, count=1, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bCOMMIT\s*;\s*$", "", cleaned, flags=re.IGNORECASE | re.DOTALL)

    statements: list[str] = []
    buf: list[str] = []
    in_dollar = False
    dollar_tag = ""
    in_single = False
    i = 0
    text = cleaned

    while i < len(text):
        ch = text[i]

        if in_dollar:
            if text.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                in_dollar = False
                dollar_tag = ""
                continue
            buf.append(ch)
            i += 1
            continue

        if in_single:
            buf.append(ch)
            if ch == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue

        # Dollar-quoted bodies (funciones PL/pgSQL)
        if ch == "$":
            m = re.match(r"\$([A-Za-z_]*)\$", text[i:])
            if m:
                dollar_tag = m.group(0)
                in_dollar = True
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue

        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def load_admission_ddl() -> str:
    if not DDL_FILE.is_file():
        raise FileNotFoundError(f"DDL de admisión no encontrado: {DDL_FILE}")
    return DDL_FILE.read_text(encoding="utf-8")


def ensure_admission_schema(connection: DatabaseConnection | None = None) -> None:
    """
    Crea/migra las 5 tablas del simulador UNA + vistas/triggers.
    No altera memoria_activa ni su esquema heredado.
    """
    conn = connection or db_connection
    sql = load_admission_ddl()
    statements = _split_sql_statements(sql)
    if not statements:
        raise RuntimeError("DDL de admisión vacío tras el parseo.")

    with conn.get_cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)

    logger.info(
        "Esquema admisión UNA OK (%s sentencias). Tablas: %s",
        len(statements),
        ", ".join(ADMISSION_TABLES),
    )
