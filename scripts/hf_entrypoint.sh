#!/usr/bin/env bash
# =============================================================================
# Entrypoint Hugging Face Spaces — unifica puerto 7860 + parchea api_url
# =============================================================================
set -euo pipefail

PORT="${PORT:-7860}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
export PORT BACKEND_PORT
export APP_ENV="${APP_ENV:-production}"
export REFLEX_REDIS_URL="${REFLEX_REDIS_URL:-redis://127.0.0.1:6379}"

echo "[hf] Arranque Neuro Plataforma · PORT=${PORT} BACKEND=${BACKEND_PORT}"

# ---------------------------------------------------------------------------
# 1) URL pública del Space (browser → Caddy → backend)
#    Preferencia: PUBLIC_APP_URL > https://$SPACE_HOST > fallback localhost
# ---------------------------------------------------------------------------
resolve_public_url() {
  if [[ -n "${PUBLIC_APP_URL:-}" ]]; then
    echo "${PUBLIC_APP_URL%/}"
    return
  fi
  if [[ -n "${SPACE_HOST:-}" ]]; then
    echo "https://${SPACE_HOST}"
    return
  fi
  # Fallback solo útil en `docker run` local
  echo "http://127.0.0.1:${PORT}"
}

PUBLIC_URL="$(resolve_public_url)"
export PUBLIC_APP_URL="${PUBLIC_URL}"
export REFLEX_API_URL="${PUBLIC_URL}"
export OPENROUTER_SITE_URL="${OPENROUTER_SITE_URL:-${PUBLIC_URL}}"

echo "[hf] PUBLIC_APP_URL=${PUBLIC_URL}"

# ---------------------------------------------------------------------------
# 2) Reescribe env.json del frontend exportado (evita apuntar a __PUBLIC_HOST__)
# ---------------------------------------------------------------------------
python - <<'PY'
import json, os, pathlib, sys

public = os.environ.get("PUBLIC_APP_URL", "").rstrip("/")
placeholder = "https://__PUBLIC_HOST__"
roots = [pathlib.Path("/srv"), pathlib.Path("/app/.web")]

patched = 0
for root in roots:
    if not root.exists():
        continue
    for path in root.rglob("env.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[hf] skip {path}: {exc}", file=sys.stderr)
            continue
        changed = False
        for key in ("apiUrl", "api_url", "deployUrl", "deploy_url", "url"):
            if key in data and isinstance(data[key], str):
                val = data[key]
                if placeholder in val or "__PUBLIC_HOST__" in val or not val:
                    data[key] = public
                    changed = True
                elif val.startswith("http://localhost") or val.startswith("http://127.0.0.1"):
                    data[key] = public
                    changed = True
        # Algunas builds anidan config
        if isinstance(data.get("env"), dict):
            for key in ("apiUrl", "api_url"):
                if key in data["env"]:
                    data["env"][key] = public
                    changed = True
        if changed:
            path.write_text(json.dumps(data), encoding="utf-8")
            patched += 1
            print(f"[hf] patched {path}")

# Seguridad: también sed sobre bundles si quedó el placeholder embebido
for root in roots:
    if not root.exists():
        continue
    for path in list(root.rglob("*.js")) + list(root.rglob("*.html")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "__PUBLIC_HOST__" not in text and placeholder not in text:
            continue
        new = text.replace(placeholder, public).replace("https://__PUBLIC_HOST__", public)
        if new != text:
            path.write_text(new, encoding="utf-8")
            patched += 1
            print(f"[hf] placeholder replaced in {path.name}")

print(f"[hf] frontend patches applied: {patched}")
PY

# ---------------------------------------------------------------------------
# 3) Validación mínima de secrets (no aborta: permite UI de error amigable)
# ---------------------------------------------------------------------------
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[hf] WARN: DATABASE_URL no está definida (Secrets del Space)."
fi
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "[hf] WARN: OPENROUTER_API_KEY ausente — Mis Guías / LLM no funcionarán."
fi

# ---------------------------------------------------------------------------
# 4) Redis (state manager compartido) + Caddy (puerto único) + Reflex backend
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

# Espera breve a Redis
for i in $(seq 1 30); do
  if redis-cli ping 2>/dev/null | grep -q PONG; then
    break
  fi
  sleep 0.2
done

echo "[hf] Iniciando Caddy en :${PORT}…"
caddy start --config /app/Caddyfile --adapter caddyfile

echo "[hf] Iniciando Reflex backend-only en :${BACKEND_PORT}…"
# Backend-only: nunca pasar --frontend-port ni dejar REFLEX_FRONTEND_PORT
# (rxconfig también omite frontend_port en producción).
unset REFLEX_FRONTEND_PORT || true
export REFLEX_BACKEND_ONLY=1
cd /app
exec reflex run \
  --env prod \
  --backend-only \
  --backend-port "${BACKEND_PORT}" \
  --backend-host 0.0.0.0 \
  --loglevel info
