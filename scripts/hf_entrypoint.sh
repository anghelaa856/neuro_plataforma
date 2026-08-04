#!/usr/bin/env bash
# =============================================================================
# Entrypoint Hugging Face Spaces — arranque mínimo (sin urllib / sin Python)
# =============================================================================
# Frontend (export Docker): REFLEX_API_URL=http://localhost
#   → Reflex 0.9.7 getBackendURL reescribe "localhost" a window.location
#     (SAME_DOMAIN_HOSTNAMES) y eleva ws→wss bajo HTTPS.
# Runtime: solo exportamos CORS/API del backend y levantamos Redis+Caddy+Reflex.
# =============================================================================
set -euo pipefail

PORT="${PORT:-7860}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
export PORT BACKEND_PORT
export APP_ENV="${APP_ENV:-production}"
export REFLEX_REDIS_URL="${REFLEX_REDIS_URL:-redis://127.0.0.1:6379}"

echo "[hf] Arranque Neuro Plataforma · PORT=${PORT} BACKEND=${BACKEND_PORT}"

# ---------------------------------------------------------------------------
# Origen público del Space (solo env vars; sin parsear con urllib)
# Preferencia: PUBLIC_APP_URL → https://$SPACE_HOST → http://127.0.0.1:$PORT
# Limpia formato Markdown accidental: [https://x](https://x) → https://x
# ---------------------------------------------------------------------------
sanitize_public_url() {
  local u="${1:-}"
  # Trim espacios/comillas
  u="${u#"${u%%[![:space:]]*}"}"
  u="${u%"${u##*[![:space:]]}"}"
  u="${u#\'}"
  u="${u%\'}"
  u="${u#\"}"
  u="${u%\"}"
  # Markdown [texto](url) → url
  if [[ "${u}" == \[*\]\(*\) ]]; then
    u="${u##*](}"
    u="${u%)}"
  fi
  # Remover brackets residuales
  u="${u//\[/}"
  u="${u//\]/}"
  u="${u%/}"
  printf '%s' "${u}"
}

if [[ -n "${PUBLIC_APP_URL:-}" ]]; then
  PUBLIC_APP_URL="$(sanitize_public_url "${PUBLIC_APP_URL}")"
elif [[ -n "${SPACE_HOST:-}" ]]; then
  _host="$(sanitize_public_url "${SPACE_HOST}")"
  # Si SPACE_HOST vino como URL completa, extraer hostname a mano
  case "${_host}" in
    https://*) _host="${_host#https://}" ;;
    http://*)  _host="${_host#http://}" ;;
  esac
  _host="${_host%%/*}"
  PUBLIC_APP_URL="https://${_host}"
else
  PUBLIC_APP_URL="http://127.0.0.1:${PORT}"
fi

# Si alguien pasó wss:// por error, normalizar a https:// (bash puro)
case "${PUBLIC_APP_URL}" in
  wss://*) PUBLIC_APP_URL="https://${PUBLIC_APP_URL#wss://}" ;;
  ws://*)  PUBLIC_APP_URL="http://${PUBLIC_APP_URL#ws://}" ;;
esac

export PUBLIC_APP_URL
# Backend / CORS / OpenRouter (el frontend estático ya usa localhost→SAME_DOMAIN)
export REFLEX_API_URL="${PUBLIC_APP_URL}"
export OPENROUTER_SITE_URL="${OPENROUTER_SITE_URL:-${PUBLIC_APP_URL}}"

echo "[hf] PUBLIC_APP_URL=${PUBLIC_APP_URL}"
echo "[hf] REFLEX_API_URL=${REFLEX_API_URL}"
echo "[hf] Frontend estático: http://localhost (SAME_DOMAIN → host del navegador)"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[hf] WARN: DATABASE_URL no está definida (Secrets del Space)."
fi
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "[hf] WARN: OPENROUTER_API_KEY ausente — Mis Guías / LLM no funcionarán."
fi

# ---------------------------------------------------------------------------
# Redis (state) + Caddy (puerto único) + Reflex backend-only
# ---------------------------------------------------------------------------
mkdir -p /tmp/redis /tmp/caddy "${HOME}/.local/share/reflex" || true

echo "[hf] Iniciando Redis…"
redis-server \
  --daemonize yes \
  --port 6379 \
  --bind 127.0.0.1 \
  --dir /tmp/redis \
  --save "" \
  --appendonly no \
  --protected-mode yes \
  --loglevel notice

for _ in $(seq 1 30); do
  if redis-cli ping 2>/dev/null | grep -q PONG; then
    break
  fi
  sleep 0.2
done

echo "[hf] Iniciando Caddy en :${PORT}…"
caddy start --config /app/Caddyfile --adapter caddyfile

echo "[hf] Iniciando Reflex backend-only en :${BACKEND_PORT}…"
unset REFLEX_FRONTEND_PORT || true
export REFLEX_BACKEND_ONLY=1
cd /app
exec reflex run \
  --env prod \
  --backend-only \
  --backend-port "${BACKEND_PORT}" \
  --backend-host 0.0.0.0 \
  --loglevel info
