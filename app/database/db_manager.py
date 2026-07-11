"""
Alias temporal de compatibilidad.

La implementación real vive en app.infrastructure.database.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

from psycopg2.extras import RealDictCursor

from app.infrastructure.database.connection import db_connection
from app.infrastructure.database.repositories import memory_card_repository
from app.infrastructure.database.schema import ensure_schema as ensure_schema_fn
from app.infrastructure.database.user_repository import user_repository


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

    def fetch_memory_cards(self, usuario_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        return memory_card_repository.fetch_memory_cards(usuario_id=usuario_id, limit=limit)

    def fetch_progress_dashboard(self, usuario_id: int, due_limit: int = 20) -> Dict[str, Any]:
        return memory_card_repository.fetch_progress_dashboard(
            usuario_id=usuario_id,
            due_limit=due_limit,
        )

    def fetch_topic_latest_state(self, usuario_id: int, tema: str) -> Dict[str, Any]:
        return memory_card_repository.fetch_topic_latest_state(usuario_id=usuario_id, tema=tema)

    def fetch_due_study_cards(self, usuario_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        return memory_card_repository.fetch_due_study_cards(usuario_id=usuario_id, limit=limit)

    def register_user(self, email: str, password: str, nombre: str) -> Dict[str, Any]:
        return user_repository.create_user(email=email, password=password, nombre=nombre)

    def authenticate_user(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        return user_repository.authenticate(email=email, password=password)

    def get_user(self, usuario_id: int) -> Optional[Dict[str, Any]]:
        return user_repository.get_by_id(usuario_id)


db_manager = DatabaseManager()
