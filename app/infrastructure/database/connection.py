"""Pool de conexiones PostgreSQL seguro para Streamlit (multihilo) + Neon."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Generator, Optional

from psycopg2 import OperationalError, pool
from psycopg2.extensions import connection as PgConnection
from psycopg2.extras import RealDictCursor
from psycopg2.pool import PoolError, ThreadedConnectionPool

from config.settings import settings

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """
    Pool thread-safe para entornos como Streamlit Cloud.

    - Usa ThreadedConnectionPool (SimpleConnectionPool NO es thread-safe).
    - Devuelve conexiones con putconn seguro (evita 'unkeyed connection').
    - Descarta conexiones muertas (Neon cierra idle connections).
    """

    def __init__(self) -> None:
        self._pool: Optional[ThreadedConnectionPool] = None
        self._init_lock = threading.RLock()

    def connect(self, minconn: int = 1, maxconn: int = 3) -> None:
        """Inicializa el pool una sola vez (Neon free tolera pocos sockets)."""
        with self._init_lock:
            if self._pool is not None and not getattr(self._pool, "closed", True):
                return

            db_config = settings.postgres_config()
            # Cierra pool previo a medias si quedó inconsistente tras un redeploy.
            if self._pool is not None:
                try:
                    self._pool.closeall()
                except Exception:
                    pass
                self._pool = None

            self._pool = ThreadedConnectionPool(minconn, maxconn, **db_config)
            logger.info(
                "Pool PostgreSQL (Threaded) listo host=%s sslmode=%s maxconn=%s",
                db_config.get("host", "?"),
                db_config.get("sslmode", "disable"),
                maxconn,
            )

    def close(self) -> None:
        with self._init_lock:
            if self._pool is not None:
                try:
                    self._pool.closeall()
                except Exception as exc:
                    logger.warning("Error cerrando pool: %s", exc)
                self._pool = None
                logger.info("Pool de conexiones PostgreSQL cerrado")

    def _ensure_pool(self) -> ThreadedConnectionPool:
        if self._pool is None or getattr(self._pool, "closed", True):
            self.connect()
        if self._pool is None:
            raise RuntimeError("No se pudo inicializar el pool de conexiones.")
        return self._pool

    def _is_connection_alive(self, conn: PgConnection) -> bool:
        if conn is None or conn.closed != 0:
            return False
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception:
            return False

    def _get_healthy_conn(self) -> PgConnection:
        """Obtiene una conexión viva; descarta sockets cerrados por Neon."""
        pg_pool = self._ensure_pool()
        last_error: Optional[Exception] = None

        for _ in range(3):
            conn = pg_pool.getconn()
            try:
                if self._is_connection_alive(conn):
                    return conn
                # Conexión zombi: cerrar al devolverla al pool.
                self._safe_putconn(pg_pool, conn, close=True)
            except Exception as exc:
                last_error = exc
                self._safe_putconn(pg_pool, conn, close=True)

        raise OperationalError(
            f"No se obtuvo una conexión PostgreSQL saludable. Último error: {last_error}"
        )

    @staticmethod
    def _safe_putconn(
        pg_pool: ThreadedConnectionPool,
        conn: Optional[PgConnection],
        close: bool = False,
    ) -> None:
        """
        Devuelve la conexión al pool sin lanzar PoolError: unkeyed connection.
        Ese error aparece si el socket ya no está registrado (hilos / Neon).
        """
        if conn is None:
            return
        try:
            pg_pool.putconn(conn, close=close)
        except PoolError as exc:
            logger.warning("putconn ignorado (%s); cerrando socket localmente", exc)
            try:
                conn.close()
            except Exception:
                pass
        except Exception as exc:
            logger.warning("putconn falló (%s); intentando close()", exc)
            try:
                conn.close()
            except Exception:
                pass

    @contextmanager
    def get_cursor(self) -> Generator[RealDictCursor, None, None]:
        pg_pool = self._ensure_pool()
        conn: Optional[PgConnection] = None
        try:
            conn = self._get_healthy_conn()
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                yield cursor
            conn.commit()
        except Exception:
            if conn is not None and conn.closed == 0:
                try:
                    conn.rollback()
                except Exception:
                    # Si el rollback falla, forzar cierre al devolver.
                    self._safe_putconn(pg_pool, conn, close=True)
                    conn = None
                    raise
            raise
        finally:
            self._safe_putconn(pg_pool, conn, close=False)


db_connection = DatabaseConnection()
