"""
Alias temporal de compatibilidad.

La implementación real vive en app.infrastructure.database.
Este módulo mantiene la API pública DatabaseManager / db_manager
para que Streamlit y los tests existentes no se rompan.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

from psycopg2.extras import RealDictCursor

from app.infrastructure.database.connection import db_connection
from app.infrastructure.database.repositories import memory_card_repository
from app.infrastructure.database.schema import ensure_schema as ensure_schema_fn


class DatabaseManager:
    """Fachada compatible con el antiguo db_manager monolítico."""

    def connect(self, minconn: int = 1, maxconn: int = 5) -> None:
        db_connection.connect(minconn=minconn, maxconn=maxconn)

    def close(self) -> None:
        db_connection.close()

    @contextmanager
    def get_cursor(self) -> Generator[RealDictCursor, None, None]:
        with db_connection.get_cursor() as cursor:
            yield cursor

    def ensure_schema(self) -> None:
        ensure_schema_fn(db_connection)

    def insert_memory_card(self, *args: Any, **kwargs: Any) -> int:
        return memory_card_repository.insert_memory_card(*args, **kwargs)

    def fetch_memory_cards(self, limit: int = 50) -> List[Dict[str, Any]]:
        return memory_card_repository.fetch_memory_cards(limit=limit)

    def fetch_progress_dashboard(self, due_limit: int = 20) -> Dict[str, Any]:
        return memory_card_repository.fetch_progress_dashboard(due_limit=due_limit)

    def fetch_topic_latest_state(self, tema: str) -> Dict[str, Any]:
        return memory_card_repository.fetch_topic_latest_state(tema=tema)

    def fetch_due_study_cards(self, limit: int = 20) -> List[Dict[str, Any]]:
        return memory_card_repository.fetch_due_study_cards(limit=limit)


db_manager = DatabaseManager()
