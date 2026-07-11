"""Carga única de variables de entorno para toda la aplicación."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _hydrate_from_streamlit_secrets() -> None:
    """
    Streamlit Cloud expone secretos en st.secrets (TOML).
    Los copiamos a os.environ para que el resto del código siga usando os.getenv.
    """
    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if not secrets:
            return
        for key in secrets:
            try:
                value = secrets[key]
            except Exception:
                continue
            if isinstance(value, dict):
                # Soporta [postgres] db=... user=... en secrets.toml
                for sub_key, sub_val in value.items():
                    env_key = f"{key}_{sub_key}".upper()
                    if env_key not in os.environ or not os.environ.get(env_key):
                        os.environ[env_key] = str(sub_val)
            else:
                env_key = str(key)
                if env_key not in os.environ or not os.environ.get(env_key):
                    os.environ[env_key] = str(value)
    except Exception:
        # Fuera de Streamlit (tests, scripts) no hay secrets: continuar con .env
        return


_hydrate_from_streamlit_secrets()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


class Settings:
    """Valores de configuración leídos desde el entorno (.env / Secrets)."""

    def __init__(self) -> None:
        self.app_env: str = _env("APP_ENV", "development")

        # PostgreSQL (campos sueltos)
        self.postgres_db: str = _env("POSTGRES_DB", "sistema_estudio")
        self.postgres_user: str = _env("POSTGRES_USER", "postgres")
        self.postgres_password: str = _env("POSTGRES_PASSWORD", "")
        self.postgres_host: str = _env("POSTGRES_HOST", "localhost")
        self.postgres_port: str = _env("POSTGRES_PORT", "5432")
        # Neon / Supabase requieren SSL en la nube
        self.postgres_sslmode: str = _env("POSTGRES_SSLMODE", "")
        # Alternativa: URL completa
        self.database_url: str = _env("DATABASE_URL", "")

        # OpenRouter / LLM
        self.openrouter_api_key: str = _env("OPENROUTER_API_KEY", "")
        self.openrouter_model: str = _env("OPENROUTER_MODEL", "openrouter/auto")
        self.openrouter_site_url: str = _env("OPENROUTER_SITE_URL", "http://localhost")
        self.openrouter_app_name: str = _env("OPENROUTER_APP_NAME", "Neuro Plataforma")

        # NLP / anomalías
        self.nlp_model: str = _env(
            "NLP_MODEL",
            "sentence-transformers/all-MiniLM-L6-v2",
        )
        self.anomaly_contamination: float = float(_env("ANOMALY_CONTAMINATION", "0.15"))

    def postgres_config(self) -> Dict[str, Any]:
        """Parámetros listos para psycopg2 / SimpleConnectionPool."""
        if self.database_url.strip():
            return self._config_from_database_url(self.database_url.strip())

        cfg: Dict[str, Any] = {
            "dbname": self.postgres_db,
            "user": self.postgres_user,
            "password": self.postgres_password,
            "host": self.postgres_host,
            "port": self.postgres_port,
        }
        if self.postgres_sslmode.strip():
            cfg["sslmode"] = self.postgres_sslmode.strip()
        elif self.postgres_host not in {"localhost", "127.0.0.1"}:
            # Host remoto típico (Neon/Supabase): SSL por defecto
            cfg["sslmode"] = "require"
        return cfg

    @staticmethod
    def _config_from_database_url(url: str) -> Dict[str, Any]:
        parsed = urlparse(url)
        dbname = (parsed.path or "").lstrip("/") or "postgres"
        cfg: Dict[str, Any] = {
            "dbname": dbname,
            "user": parsed.username or "",
            "password": parsed.password or "",
            "host": parsed.hostname or "localhost",
            "port": str(parsed.port or 5432),
        }
        # query: sslmode=require
        if parsed.query:
            for part in parsed.query.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    if k == "sslmode":
                        cfg["sslmode"] = v
        cfg.setdefault("sslmode", "require")
        return cfg


settings = Settings()
