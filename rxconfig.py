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


def _strip_markdown_url(raw: str) -> str:
    """Normaliza URLs pegadas como Markdown: [https://x](https://x) → https://x."""
    text = (raw or "").strip().strip("`'\"")
    # [label](https://…)  o  [https://…](https://…)
    if text.startswith("[") and "](" in text and text.endswith(")"):
        inner = text[text.rfind("](") + 2 : -1].strip()
        if inner:
            text = inner
    # residuales por copiar mal el secret
    text = text.replace("[", "").replace("]", "")
    return text.strip().rstrip("/")


def _public_app_url() -> str | None:
    explicit = _strip_markdown_url(os.getenv("PUBLIC_APP_URL") or "")
    if explicit:
        return explicit
    space_host = _strip_markdown_url(os.getenv("SPACE_HOST") or "")
    # SPACE_HOST debe ser solo hostname; si trajeron una URL, quedarnos con netloc
    if space_host.startswith("http://") or space_host.startswith("https://"):
        space_host = space_host.split("://", 1)[-1].split("/", 1)[0]
    if space_host:
        return f"https://{space_host}"
    return None


def _cors_origins(*, production: bool) -> tuple[str, ...]:
    """Orígenes CORS para Starlette + Engine.IO (WebSocket).

    Crítico en Reflex 0.9.7 (reflex/app.py ~540):
      Se pasa a AsyncServer el string \"*\" SOLO si
      config.cors_allowed_origins == (\"*\",)  # tuple exacto
    Una lista [\"*\"] se interpreta como origen literal \"*\" y el dominio
    https://….hf.space recibe 403 (\"is not an accepted origin\").
    """
    del production  # mismo criterio local/prod detrás de proxy o LAN
    return ("*",)


IS_PROD = _is_production()
PUBLIC_URL = _public_app_url()
CORS_ALLOWED_ORIGINS = _cors_origins(production=IS_PROD)

# Local: frontend 3005 / backend 8005 (dev).
# Contenedor HF: Caddy publica :7860; Reflex corre SOLO backend en :8000.
# Importante: en prod NO fijar frontend_port — Reflex 0.9.7 aborta
# \"Cannot specify --frontend-port when not running frontend\" con --backend-only.
if IS_PROD:
    PUBLIC_PORT = int(os.getenv("PORT", "7860"))  # solo Caddy
    BACKEND_PORT = int(os.getenv("BACKEND_PORT", "8000"))
    FRONTEND_PORT = None
    # Runtime: PUBLIC_APP_URL / SPACE_HOST para api_url del backend.
    # Build/export: sin SPACE_HOST → http://localhost (SAME_DOMAIN en el cliente).
    if PUBLIC_URL:
        API_URL = PUBLIC_URL
    else:
        API_URL = _strip_markdown_url(
            os.getenv("REFLEX_API_URL") or "http://localhost"
        ) or "http://localhost"
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


_config_kwargs: dict = {
    "app_name": "neuro_plataforma",
    "host": "0.0.0.0",
    "backend_host": "0.0.0.0",
    "backend_port": BACKEND_PORT,
    "api_url": API_URL,
    "deploy_url": DEPLOY_URL,
    "vite_allowed_hosts": True,
    # Debe ser exactamente ("*",) para que Engine.IO acepte cualquier Origin HF
    "cors_allowed_origins": CORS_ALLOWED_ORIGINS,
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
