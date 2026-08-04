#!/usr/bin/env bash
# =============================================================================
# Entrypoint Hugging Face Spaces
# Caddy :$PORT  →  estáticos /srv  +  proxy backend :$BACKEND_PORT
# =============================================================================
# Frontend exportado con api_url=http://localhost (ver Dockerfile).
# Reflex 0.9.7 (getBackendURL) detecta hostname "localhost" en
# SAME_DOMAIN_HOSTNAMES y lo sustituye por window.location (el Space real),
# elevando ws→wss / http→https. NO hace falta sed/parche destructivo en .js.
# =============================================================================
set -euo pipefail

PORT="${PORT:-7860}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
export PORT BACKEND_PORT
export APP_ENV="${APP_ENV:-production}"
export REFLEX_REDIS_URL="${REFLEX_REDIS_URL:-redis://127.0.0.1:6379}"

echo "[hf] Arranque Neuro Plataforma · PORT=${PORT} BACKEND=${BACKEND_PORT}"

# ---------------------------------------------------------------------------
# 1) Origen público absoluto (solo para CORS / OpenRouter / backend)
# ---------------------------------------------------------------------------
resolve_public_url() {
  local raw=""
  if [[ -n "${PUBLIC_APP_URL:-}" ]]; then
    raw="${PUBLIC_APP_URL}"
  elif [[ -n "${SPACE_HOST:-}" ]]; then
    raw="https://${SPACE_HOST}"
  else
    raw="http://127.0.0.1:${PORT}"
  fi
  # Normalizar: esquema http(s), sin slash final, sin ws/wss
  raw="${raw%/}"
  case "${raw}" in
    wss://*) raw="https://${raw#wss://}" ;;
    ws://*)  raw="http://${raw#ws://}" ;;
  esac
  # Si vino sin esquema, asumir https en Spaces
  case "${raw}" in
    http://*|https://*) ;;
    *) raw="https://${raw}" ;;
  esac
  echo "${raw}"
}

PUBLIC_URL="$(resolve_public_url)"
export PUBLIC_APP_URL="${PUBLIC_URL}"
export REFLEX_API_URL="${PUBLIC_URL}"
export OPENROUTER_SITE_URL="${OPENROUTER_SITE_URL:-${PUBLIC_URL}}"

python - <<'PY'
"""Valida PUBLIC_APP_URL y sana solo env.json (nunca toca bundles .js)."""
from __future__ import annotations

import json
import os
import pathlib
import sys
from urllib.parse import urlparse

public = (os.environ.get("PUBLIC_APP_URL") or "").strip().rstrip("/")
parsed = urlparse(public)
if parsed.scheme not in {"http", "https"} or not parsed.netloc:
    print(f"[hf] ERROR: PUBLIC_APP_URL inválida para getBackendURL/CORS: {public!r}", file=sys.stderr)
    sys.exit(1)

print(f"[hf] PUBLIC_APP_URL={public} (válida)")

# Origen "localhost" que el cliente reescribe a window.location (SAME_DOMAIN)
LOCAL_HTTP = "http://localhost"
LOCAL_WS = "ws://localhost"

MARKERS = ("public_host", "neuro-placeholder", "__public_host__", "__PUBLIC_HOST__")


def is_absolute_http_or_ws(value: str) -> bool:
    p = urlparse(value)
    return p.scheme in {"http", "https", "ws", "wss"} and bool(p.netloc)


def localize_endpoint(key: str, value: str) -> str:
    """Convierte placeholders / URLs rotas a localhost absoluto (válido para new URL())."""
    k = key.lower()
    v = value or ""
    path = urlparse(v).path if is_absolute_http_or_ws(v) else ""

    if "event" in k or path.endswith("/_event") or "/_event" in v:
        return f"{LOCAL_WS}/_event"
    if "upload" in k or "/_upload" in v:
        return f"{LOCAL_HTTP}/_upload"
    if "ping" in k or v.endswith("/ping") or "/ping" in path:
        return f"{LOCAL_HTTP}/ping"
    if "health" in k or "/_health" in v:
        return f"{LOCAL_HTTP}/_health"
    if "all_routes" in k or "/_all_routes" in v:
        return f"{LOCAL_HTTP}/_all_routes"
    if "auth" in k:
        return f"{LOCAL_HTTP}/auth-codespace"
    if k in {"apiurl", "api_url", "deployurl", "deploy_url", "url"}:
        return LOCAL_HTTP
    return LOCAL_HTTP


def needs_heal(value: str) -> bool:
    if not value or not isinstance(value, str):
        return True
    low = value.lower()
    if any(m.lower() in low for m in MARKERS):
        return True
    if not is_absolute_http_or_ws(value):
        return True
    host = urlparse(value).hostname or ""
    if "_" in host:
        return True
    return False


def heal_obj(data: dict) -> bool:
    changed = False
    for key, val in list(data.items()):
        if isinstance(val, dict):
            if heal_obj(val):
                changed = True
            continue
        if not isinstance(val, str):
            continue
        if needs_heal(val):
            new_v = localize_endpoint(str(key), val)
            if new_v != val:
                data[key] = new_v
                changed = True
                print(f"[hf] env.json[{key!r}]: {val!r} → {new_v!r}")
    for key, default in (
        ("apiUrl", LOCAL_HTTP),
        ("deployUrl", LOCAL_HTTP),
        ("EVENT", f"{LOCAL_WS}/_event"),
        ("PING", f"{LOCAL_HTTP}/ping"),
    ):
        if key in data and isinstance(data[key], str) and needs_heal(data[key]):
            data[key] = default
            changed = True
    return changed


healed = 0
for root in (pathlib.Path("/srv"), pathlib.Path("/app/.web")):
    if not root.exists():
        continue
    for path in root.rglob("env.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[hf] skip {path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            continue
        if heal_obj(data):
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            healed += 1
            print(f"[hf] env.json sanado (localhost SAME_DOMAIN): {path}")

print(f"[hf] env.json sanados: {healed} · JS bundles: sin modificación")
PY

# ---------------------------------------------------------------------------
# 2) Secrets (aviso, no aborta)
# ---------------------------------------------------------------------------
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[hf] WARN: DATABASE_URL no está definida (Secrets del Space)."
fi
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "[hf] WARN: OPENROUTER_API_KEY ausente — Mis Guías / LLM no funcionarán."
fi

# ---------------------------------------------------------------------------
# 3) Redis + Caddy + Reflex backend-only
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

for i in $(seq 1 30); do
  if redis-cli ping 2>/dev/null | grep -q PONG; then
    break
  fi
  sleep 0.2
done

echo "[hf] Iniciando Caddy en :${PORT}…"
caddy start --config /app/Caddyfile --adapter caddyfile

echo "[hf] Iniciando Reflex backend-only en :${BACKEND_PORT}…"
echo "[hf] REFLEX_API_URL=${REFLEX_API_URL}"
unset REFLEX_FRONTEND_PORT || true
export REFLEX_BACKEND_ONLY=1
cd /app
exec reflex run \
  --env prod \
  --backend-only \
  --backend-port "${BACKEND_PORT}" \
  --backend-host 0.0.0.0 \
  --loglevel info
