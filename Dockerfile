# syntax=docker/dockerfile:1.6
# =============================================================================
# Neuro Plataforma — Dockerfile de producción para Hugging Face Spaces (Docker)
# =============================================================================
# Arquitectura (un solo puerto público = HF app_port 7860):
#
#   Internet ──► Caddy :7860 ─┬─► estáticos (/srv)          frontend exportado
#                             └─► Reflex backend :8000      /_event, /ping, …
#   Redis :6379  ← state compartido (varios estudiantes / WebSockets)
#   Neon Postgres ← DATABASE_URL (Secret de HF, fuera del contenedor)
#
# Build: HF construye este Dockerfile automáticamente al hacer push al Space.
# =============================================================================

ARG PORT=7860
# Placeholder de build: se reescribe en runtime con SPACE_HOST / PUBLIC_APP_URL.
ARG API_URL=https://__PUBLIC_HOST__

# ---------------------------------------------------------------------------
# Stage 1 — builder: deps + export estático del frontend Reflex
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Herramientas mínimas para bun/node que instala `reflex init`
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# 1) Solo requirements → máxima reutilización de caché de capas
COPY requirements.txt .
RUN pip install -r requirements.txt

# 2) Config primero → `reflex init` cacheable si no cambian deps/config
COPY rxconfig.py ./
RUN reflex init

# 3) Código de la app
COPY . .

ARG PORT API_URL
ENV PORT=${PORT} \
    REFLEX_API_URL=${API_URL} \
    APP_ENV=production

# Compila frontend y deja artefactos en /srv (sin zip)
RUN mkdir -p /srv \
    && reflex export --frontend-only --no-zip --loglevel info \
    && cp -a .web/build/client/. /srv/ \
    && rm -rf .web node_modules /root/.npm /root/.cache \
    && find /app/.venv -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true


# ---------------------------------------------------------------------------
# Stage 2 — runtime: Caddy + Redis + backend Reflex (usuario no-root UID 1000)
# ---------------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

ARG PORT=7860
ARG API_URL=https://__PUBLIC_HOST__

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=${PORT} \
    BACKEND_PORT=8000 \
    REFLEX_API_URL=${API_URL} \
    REFLEX_REDIS_URL=redis://127.0.0.1:6379 \
    APP_ENV=production \
    # Pool Neon (ajusta vía Secret PG_POOL_MAX si hace falta)
    PG_POOL_MIN=1 \
    PG_POOL_MAX=12 \
    HOME=/home/user \
    PATH="/app/.venv/bin:/home/user/.local/bin:$PATH"

# Caddy (proxy único), Redis (state), utilidades PDF/OCR usadas por Mis Guías
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        caddy \
        redis-server \
        ca-certificates \
        curl \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-spa \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 user \
    && mkdir -p /app /srv /tmp/redis /tmp/caddy /tmp/uploads \
    && chown -R user:user /app /srv /tmp/redis /tmp/caddy /tmp/uploads /home/user

WORKDIR /app

# venv + código + frontend estático + proxy config
COPY --from=builder --chown=user:user /app /app
COPY --from=builder --chown=user:user /srv /srv
COPY --chown=user:user Caddyfile /app/Caddyfile
COPY --chown=user:user scripts/hf_entrypoint.sh /app/scripts/hf_entrypoint.sh

# Asegura permisos de ejecución del entrypoint
RUN chmod +x /app/scripts/hf_entrypoint.sh \
    && chown -R user:user /app /srv

USER user

# HF Spaces (y healthchecks) solo publican este puerto
EXPOSE 7860

# HF puede tardar en el primer arranque (export ya hecho; backend + redis)
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=5 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null || exit 1

# STOPSIGNAL agresivo heredado del ejemplo oficial (backend no reenvía SIGTERM limpio)
STOPSIGNAL SIGKILL

CMD ["/app/scripts/hf_entrypoint.sh"]
