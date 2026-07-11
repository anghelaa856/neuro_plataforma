"""Pool de conexiones PostgreSQL."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator, Optional

from psycopg2 import pool
from psycopg2.extras import RealDictCursor

from config.settings import settings

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Gestiona el pool de conexiones a PostgreSQL."""

    def __init__(self) -> None:
        self._pool: Optional[pool.SimpleConnectionPool] = None

    def connect(self, minconn: int = 1, maxconn: int = 5) -> None:
        """Inicializa el pool si aún no existe."""
        if self._pool is not None:
            return

        db_config = settings.postgres_config()
        self._pool = pool.SimpleConnectionPool(minconn, maxconn, **db_config)
        host = db_config.get("host", "?")
        sslmode = db_config.get("sslmode", "disable")
        logger.info(
            "Pool PostgreSQL inicializado (host=%s, sslmode=%s)",
            host,
            sslmode,
        )

    def close(self) -> None:
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None
            logger.info("Pool de conexiones PostgreSQL cerrado")

    @contextmanager
    def get_cursor(self) -> Generator[RealDictCursor, None, None]:
        if self._pool is None:
            self.connect()

        if self._pool is None:
            raise RuntimeError("No se pudo inicializar el pool de conexiones.")

        conn = self._pool.getconn()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)


db_connection = DatabaseConnection()
