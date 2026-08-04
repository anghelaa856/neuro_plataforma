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

# Local: frontend 3005 / backend 8005 (dev). Contenedor: Caddy 7860 → backend 8000.
if IS_PROD:
    FRONTEND_PORT = int(os.getenv("PORT", os.getenv("FRONTEND_PORT", "7860")))
    BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
    # El browser habla con el origen público (mismo host que Caddy).
    API_URL = PUBLIC_URL or os.getenv("REFLEX_API_URL") or f"http://127.0.0.1:{FRONTEND_PORT}"
    DEPLOY_URL = API_URL
    REDIS_URL = os.getenv("REFLEX_REDIS_URL", "redis://127.0.0.1:6379")
    STATE_MODE = "redis"
    CORS = [API_URL] if API_URL.startswith("https://") else ["*"]
else:
    FRONTEND_PORT = int(os.getenv("FRONTEND_PORT", "3005"))
    BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8005"))
    # Dev en LAN: override con REFLEX_API_URL o deja el host de tu PC.
    API_URL = os.getenv("REFLEX_API_URL", "http://192.168.100.5:8005")
    DEPLOY_URL = os.getenv("REFLEX_DEPLOY_URL", "http://192.168.100.5:3005")
    REDIS_URL = os.getenv("REFLEX_REDIS_URL")  # opcional en local
    STATE_MODE = "redis" if REDIS_URL else "disk"
    CORS = ["*"]


config = rx.Config(
    app_name="neuro_plataforma",
    host="0.0.0.0",
    backend_host="0.0.0.0",
    frontend_port=FRONTEND_PORT,
    backend_port=BACKEND_PORT,
    api_url=API_URL,
    deploy_url=DEPLOY_URL,
    # Imprescindible detrás de reverse-proxy / HF / túneles
    vite_allowed_hosts=True,
    cors_allowed_origins=CORS,
    # Redis en prod → state compartido entre conexiones (simulacros concurrentes)
    redis_url=REDIS_URL,
    state_manager_mode=STATE_MODE,
    # Telemetría off en Spaces
    show_built_with_reflex=not IS_PROD and _env_bool("SHOW_BUILT_WITH_REFLEX", False),
    plugins=[
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
)
