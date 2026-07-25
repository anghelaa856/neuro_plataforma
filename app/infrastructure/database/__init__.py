"""Capa de acceso a PostgreSQL."""

from app.infrastructure.database.admission_schema import ensure_admission_schema
from app.infrastructure.database.banco_repository import BancoRepository, banco_repository
from app.infrastructure.database.connection import DatabaseConnection, db_connection
from app.infrastructure.database.repositories import MemoryCardRepository, memory_card_repository
from app.infrastructure.database.schema import ensure_schema
from app.infrastructure.database.seed_catalogo_materias import seed_catalogo_materias
from app.infrastructure.database.user_repository import UserRepository, user_repository

__all__ = [
    "DatabaseConnection",
    "db_connection",
    "ensure_schema",
    "ensure_admission_schema",
    "seed_catalogo_materias",
    "BancoRepository",
    "banco_repository",
    "MemoryCardRepository",
    "memory_card_repository",
    "UserRepository",
    "user_repository",
]
