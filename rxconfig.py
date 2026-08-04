"""Configuración Reflex — local (LAN) vs producción (HF Spaces / Docker)."""

from __future__ import annotations

import os

import reflex as rx


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_production() -> bool:
    if os.getenv("SPACE_ID") or os.getenv("SPACE_HOST"):
        return True
    return os.getenv("APP_ENV", "development").strip().lower() in {
        "prod",
        "production",
    }


def _public_app_url() -> str | None:
    explicit = (os.getenv("PUBLIC_APP_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    space_host = (os.getenv("SPACE_HOST") or "").strip()
    if space_host:
        return f"https://{space_host}"
    return None


IS_PROD = _is_production()
PUBLIC_URL = _public_app_url()

# Local: frontend 3005 / backend 8005 (dev).
# Contenedor HF: Caddy publica :7860; Reflex corre SOLO backend en :8000.
# Importante: en prod NO fijar frontend_port — Reflex 0.9.7 hace:
#   frontend_port = cli_or_config.frontend_port
# y aborta con "Cannot specify --frontend-port when not running frontend"
# si se usa --backend-only con frontend_port distinto de None.
if IS_PROD:
    PUBLIC_PORT = int(os.getenv("PORT", "7860"))  # solo Caddy
    BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
    FRONTEND_PORT = None
    # Build/export: REFLEX_API_URL=http://localhost → frontend SAME_DOMAIN.
    # Runtime HF: PUBLIC_URL / SPACE_HOST → CORS y backend con origen real.
    _env_api = (os.getenv("REFLEX_API_URL") or "").strip().rstrip("/")
    if PUBLIC_URL and "public_host" not in PUBLIC_URL.lower():
        API_URL = PUBLIC_URL
        CORS = [PUBLIC_URL]
    elif _env_api and "public_host" not in _env_api.lower() and "_" not in (
        _env_api.split("://", 1)[-1].split("/", 1)[0]
    ):
        API_URL = _env_api
        CORS = ["*"] if "localhost" in API_URL or "127.0.0.1" in API_URL else [API_URL]
    else:
        API_URL = "http://localhost"
        CORS = ["*"]
    DEPLOY_URL = API_URL
    REDIS_URL = os.getenv("REFLEX_REDIS_URL", "redis://127.0.0.1:6379")
    STATE_MODE = "redis"
else:
    PUBLIC_PORT = int(os.getenv("FRONTEND_PORT", "3005"))
    FRONTEND_PORT = PUBLIC_PORT
    BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8005"))
    API_URL = os.getenv("REFLEX_API_URL", "http://192.168.100.5:8005")
    DEPLOY_URL = os.getenv("REFLEX_DEPLOY_URL", "http://192.168.100.5:3005")
    REDIS_URL = os.getenv("REFLEX_REDIS_URL")  # opcional en local
    STATE_MODE = "redis" if REDIS_URL else "disk"
    CORS = ["*"]


_config_kwargs: dict = {
    "app_name": "neuro_plataforma",
    "host": "0.0.0.0",
    "backend_host": "0.0.0.0",
    "backend_port": BACKEND_PORT,
    "api_url": API_URL,
    "deploy_url": DEPLOY_URL,
    "vite_allowed_hosts": True,
    "cors_allowed_origins": CORS,
    "redis_url": REDIS_URL,
    "state_manager_mode": STATE_MODE,
    "show_built_with_reflex": (not IS_PROD)
    and _env_bool("SHOW_BUILT_WITH_REFLEX", False),
    "plugins": [
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
        rx.plugins.RadixThemesPlugin(
            theme=rx.theme(
                appearance="light",
                has_background=True,
                radius="large",
                accent_color="blue",
                scaling="100%",
            )
        ),
    ],
}

# Solo en local pasamos frontend_port (Vite). En prod backend-only → omitir.
if FRONTEND_PORT is not None:
    _config_kwargs["frontend_port"] = FRONTEND_PORT

config = rx.Config(**_config_kwargs)
