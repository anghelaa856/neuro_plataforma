#!/usr/bin/env bash
# =============================================================================
# Entrypoint Hugging Face Spaces — unifica puerto 7860 + parchea api_url / WS
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
#    El cliente convierte https://… → wss://…/_event automáticamente.
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
  echo "http://127.0.0.1:${PORT}"
}

PUBLIC_URL="$(resolve_public_url)"
# Normalizar: siempre origen http(s) sin slash final (NO wss:// en api_url)
case "${PUBLIC_URL}" in
  wss://*) PUBLIC_URL="https://${PUBLIC_URL#wss://}" ;;
  ws://*)  PUBLIC_URL="http://${PUBLIC_URL#ws://}" ;;
esac
PUBLIC_URL="${PUBLIC_URL%/}"

export PUBLIC_APP_URL="${PUBLIC_URL}"
export REFLEX_API_URL="${PUBLIC_URL}"
export OPENROUTER_SITE_URL="${OPENROUTER_SITE_URL:-${PUBLIC_URL}}"

echo "[hf] PUBLIC_APP_URL=${PUBLIC_URL}"

# ---------------------------------------------------------------------------
# 2) Parchear frontend exportado
#    En el build se hornea un placeholder. Reflex lowercassea hostnames al armar
#    el WebSocket → wss://__public_host__/_event. Hay que reemplazar TODAS las
#    variantes (mayúsculas/minúsculas) y forzar apiUrl en env.json.
# ---------------------------------------------------------------------------
python - <<'PY'
from __future__ import annotations

import json
import os
import pathlib
import re
import sys

public = (os.environ.get("PUBLIC_APP_URL") or "").rstrip("/")
if not public:
    print("[hf] ERROR: PUBLIC_APP_URL vacío; no se puede parchear el WebSocket.", file=sys.stderr)
    sys.exit(1)

# Tokens que pueden quedar horneados tras `reflex export` / lowercasing de host
TOKEN_RE = re.compile(
    r"(?:https?|wss?)://__public_host__|__PUBLIC_HOST__|__public_host__",
    re.IGNORECASE,
)
# También el placeholder del Dockerfile (por si se cambia el token)
ALT_RE = re.compile(
    r"(?:https?|wss?)://neuro-placeholder\.invalid|neuro-placeholder\.invalid",
    re.IGNORECASE,
)

roots = [pathlib.Path("/srv"), pathlib.Path("/app/.web")]
patched_files = 0
remaining_hits = 0


def rewrite_text(text: str) -> str:
    host = public.split("://", 1)[-1]

    def _repl_token(match: re.Match[str]) -> str:
        matched = match.group(0)
        low = matched.lower()
        # Preservar esquema: wss://__public_host__ → wss://host.real
        if low.startswith("wss://"):
            return f"wss://{host}"
        if low.startswith("ws://"):
            return f"ws://{host}"
        if low.startswith("http://") or low.startswith("https://"):
            return public
        # Token desnudo → origen https/http (apiUrl)
        return public

    out = TOKEN_RE.sub(_repl_token, text)
    out = ALT_RE.sub(public, out)
    return out


def force_env_json(data: dict) -> dict:
    """Fuerza api/deploy al origen público real (Reflex lee esto en runtime)."""
    for key in ("apiUrl", "api_url", "deployUrl", "deploy_url", "url"):
        data[key] = public
    if isinstance(data.get("env"), dict):
        for key in ("apiUrl", "api_url", "deployUrl", "deploy_url"):
            data["env"][key] = public
    # Recorre un nivel más por si hay anidación rara
    for k, v in list(data.items()):
        if isinstance(v, str) and TOKEN_RE.search(v):
            data[k] = rewrite_text(v)
    return data


for root in roots:
    if not root.exists():
        continue

    for path in root.rglob("env.json"):
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as exc:
            print(f"[hf] skip {path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            continue
        data = force_env_json(data)
        path.write_text(json.dumps(data), encoding="utf-8")
        patched_files += 1
        print(f"[hf] env.json forzado → {public} ({path})")

    for path in list(root.rglob("*.js")) + list(root.rglob("*.mjs")) + list(root.rglob("*.html")) + list(root.rglob("*.json")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if not (TOKEN_RE.search(text) or ALT_RE.search(text) or "__public_host__" in text.lower()):
            continue
        new = rewrite_text(text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            patched_files += 1
            print(f"[hf] placeholder reemplazado en {path.relative_to(root)}")

# Verificación: no debe quedar el token en /srv
srv = pathlib.Path("/srv")
if srv.exists():
    for path in srv.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".js", ".mjs", ".html", ".json", ".css", ".map", ""}:
            continue
        try:
            sample = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "__public_host__" in sample.lower() or "neuro-placeholder.invalid" in sample.lower():
            remaining_hits += 1
            print(f"[hf] WARN residual: {path}", file=sys.stderr)

print(f"[hf] archivos parcheados: {patched_files} · residuales: {remaining_hits}")
if remaining_hits:
    # No abortamos el arranque (Caddy/backend aún sirven), pero dejamos traza clara
    print("[hf] WARN: aún hay placeholders; revisa el build/export.", file=sys.stderr)
PY

# ---------------------------------------------------------------------------
# 3) Validación mínima de secrets
# ---------------------------------------------------------------------------
if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[hf] WARN: DATABASE_URL no está definida (Secrets del Space)."
fi
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "[hf] WARN: OPENROUTER_API_KEY ausente — Mis Guías / LLM no funcionarán."
fi

# ---------------------------------------------------------------------------
# 4) Redis + Caddy + Reflex backend-only
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
unset REFLEX_FRONTEND_PORT || true
export REFLEX_BACKEND_ONLY=1
cd /app
exec reflex run \
  --env prod \
  --backend-only \
  --backend-port "${BACKEND_PORT}" \
  --backend-host 0.0.0.0 \
  --loglevel info
