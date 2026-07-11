"""CRUD de usuarios (registro / login)."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any, Dict, Optional

from app.infrastructure.database.connection import DatabaseConnection, db_connection


class UserRepository:
    """Gestión de cuentas de estudiante."""

    def __init__(self, connection: Optional[DatabaseConnection] = None) -> None:
        self._connection = connection or db_connection

    @staticmethod
    def _normalize_email(email: str) -> str:
        return (email or "").strip().lower()

    @staticmethod
    def hash_password(password: str, salt: Optional[str] = None) -> str:
        """PBKDF2-SHA256. Formato: salt_hex$hash_hex."""
        if salt is None:
            salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            120_000,
        )
        return f"{salt}${digest.hex()}"

    @classmethod
    def verify_password(cls, password: str, stored: str) -> bool:
        try:
            salt, _ = stored.split("$", 1)
        except ValueError:
            return False
        candidate = cls.hash_password(password, salt=salt)
        return hmac.compare_digest(candidate, stored)

    def create_user(self, email: str, password: str, nombre: str) -> Dict[str, Any]:
        # Garantiza que la tabla exista aunque el deploy anterior no migró.
        from app.infrastructure.database.schema import ensure_schema

        ensure_schema(self._connection)

        email_n = self._normalize_email(email)
        nombre_n = " ".join((nombre or "").strip().split())
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_n):
            raise ValueError("Email inválido.")
        if len(password) < 6:
            raise ValueError("La contraseña debe tener al menos 6 caracteres.")
        if len(nombre_n) < 2:
            raise ValueError("El nombre debe tener al menos 2 caracteres.")

        password_hash = self.hash_password(password)
        query = """
        INSERT INTO usuarios (email, nombre, password_hash)
        VALUES (%s, %s, %s)
        RETURNING id_usuario, email, nombre, creado_en;
        """
        try:
            with self._connection.get_cursor() as cur:
                cur.execute(query, (email_n, nombre_n, password_hash))
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("No se pudo crear el usuario.")
                return dict(row)
        except Exception as exc:
            # Postgres puede devolver el mensaje en español ("unicidad") o inglés ("unique").
            pgcode = getattr(exc, "pgcode", None)
            msg = str(exc).lower()
            if pgcode == "23505" or "unique" in msg or "unicidad" in msg or "duplicate" in msg:
                raise ValueError("Ya existe una cuenta con ese email.") from exc
            raise

    def authenticate(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        email_n = self._normalize_email(email)
        query = """
        SELECT id_usuario, email, nombre, password_hash, creado_en
        FROM usuarios
        WHERE email = %s
        LIMIT 1;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, (email_n,))
            row = cur.fetchone()
        if not row:
            return None
        payload = dict(row)
        if not self.verify_password(password, str(payload.get("password_hash") or "")):
            return None
        payload.pop("password_hash", None)
        return payload

    def get_by_id(self, usuario_id: int) -> Optional[Dict[str, Any]]:
        query = """
        SELECT id_usuario, email, nombre, creado_en
        FROM usuarios
        WHERE id_usuario = %s
        LIMIT 1;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, (int(usuario_id),))
            row = cur.fetchone()
        return dict(row) if row else None


user_repository = UserRepository()
