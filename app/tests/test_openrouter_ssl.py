"""Prueba rápida de conexión SSL hacia OpenRouter."""

from __future__ import annotations

import sys
from pathlib import Path

import certifi
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.infrastructure.ssl_config import ssl_verify
from config.settings import settings


def main() -> None:
    key = (settings.openrouter_api_key or "").strip()
    verify = ssl_verify()
    print(f"certifi CA bundle: {certifi.where()}")
    print(f"requests verify= {verify!r}")

    if not key or key.lower().startswith("your_"):
        print("SSL TEST BLOQUEADO: OPENROUTER_API_KEY ausente o placeholder en .env")
        raise SystemExit(1)

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.openrouter_site_url,
        "X-Title": settings.openrouter_app_name,
    }
    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": "Responde solo con la palabra Hola."},
            {"role": "user", "content": "Hola"},
        ],
        "temperature": 0,
        "max_tokens": 16,
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=60,
            verify=verify,
        )
        response.raise_for_status()
        data = response.json()
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        print("SSL TEST EXITOSO: conexión segura a OpenRouter OK")
        print(f"HTTP {response.status_code} | respuesta modelo: {content!r}")
    except requests.exceptions.SSLError as exc:
        print("SSL TEST FALLIDO: conexión SSL bloqueada")
        print(f"Detalle: {exc}")
        raise SystemExit(2) from exc
    except Exception as exc:
        print("SSL TEST FALLIDO: error al llamar OpenRouter (no necesariamente SSL)")
        print(f"{type(exc).__name__}: {exc}")
        raise SystemExit(3) from exc


if __name__ == "__main__":
    main()
