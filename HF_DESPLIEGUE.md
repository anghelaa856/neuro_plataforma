# Despliegue Hugging Face Spaces (Docker) — Neuro Plataforma / Reflex 0.9.7

## Arquitectura de puertos

| Rol | Puerto | Visible fuera del contenedor |
|-----|--------|------------------------------|
| **Caddy** (único público) | **7860** | Sí (`app_port`) |
| Reflex backend | 8000 | No (solo localhost) |
| Redis (state) | 6379 | No (solo localhost) |

El navegador habla solo con `https://TU-SPACE.hf.space`. Caddy reparte:
estáticos → frontend exportado; `/_event`, `/ping`, `/_upload`, … → backend.

## 1) Crear el Space

1. En [huggingface.co/new-space](https://huggingface.co/new-space) crea un Space.
2. **SDK = Docker**.
3. (Pro) elige hardware si lo necesitas; CPU basic suele bastar al inicio.
4. Conecta este repo (o sube los archivos) al Space.

El `README.md` del repo ya incluye el YAML:

```yaml
sdk: docker
app_port: 7860
```

## 2) Secrets (Variables and secrets)

En el Space: **Settings → Variables and secrets → New secret**

| Nombre | Obligatorio | Valor |
|--------|-------------|--------|
| `DATABASE_URL` | Sí | DSN Neon **con pooler** + `?sslmode=require` |
| `OPENROUTER_API_KEY` | Sí (Mis Guías) | `sk-or-v1-…` |
| `OPENROUTER_MODEL` | No | p.ej. `openrouter/auto` |
| `OPENROUTER_APP_NAME` | No | `Neuro Plataforma` |
| `PUBLIC_APP_URL` | Recomendado | `https://anghelaperez-neuro-plataforma.hf.space` (string limpio, **sin** markdown `[..](..)`) |
| `PG_POOL_MAX` | No | `12` (sube con cuidado; mide límites Neon) |

**No** subas `.env` al Space. Los Secrets se inyectan como variables de entorno
en runtime (el `Dockerfile` no debe contener claves).

> Tip Neon: usa el host `….**-pooler**.….neon.tech` para absorber muchos
> simulacros/prácticas a la vez sin agotar conexiones directas.

## 3) Archivos clave que deben estar en el repo del Space

- `Dockerfile`
- `Caddyfile`
- `scripts/hf_entrypoint.sh`
- `rxconfig.py`
- `requirements.txt`
- `neuro_plataforma/` + `app/` + `config/`
- `README.md` (con YAML Docker)
- `.dockerignore`

## 4) Push / rebuild

```bash
git add Dockerfile Caddyfile scripts/hf_entrypoint.sh rxconfig.py .dockerignore .gitattributes README.md HF_DESPLIEGUE.md
git commit -m "Deploy: Reflex one-port Docker Space (Caddy+Redis) for HF"
git push
```

Si el Space está enlazado al repo, HF reconstruye solo. Si no:

```bash
# Opción Hugging Face CLI
huggingface-cli upload TU-USER/TU-SPACE . --repo-type=space
```

Espera el build (varios minutos la primera vez: export de frontend + deps).

## 5) Comprobar salud

1. Space en estado **Running**.
2. Abre `https://TU-USER-TU-SPACE.hf.space`.
3. Login / catálogo de materias → Neon OK.
4. Una práctica corta → WebSocket OK (Caddy → `:8000`).
5. Logs del Space: no debe aparecer `DATABASE_URL no está definida`.

## 6) Prueba local del contenedor (opcional)

```bash
docker build -t neuro-plataforma:hf .
docker run --rm -p 7860:7860 \
  -e DATABASE_URL="postgresql://…?sslmode=require" \
  -e OPENROUTER_API_KEY="sk-or-…" \
  -e PUBLIC_APP_URL="http://127.0.0.1:7860" \
  neuro-plataforma:hf
```

Abre http://127.0.0.1:7860

## Fallos frecuentes

| Síntoma | Causa / arreglo |
|---------|------------------|
| Space en *Building* eterno | Mira logs; suele ser `reflex export` o red a npm/bun |
| *Runtime error* / no abre | App no escucha `0.0.0.0:7860` → revisa Caddy/entrypoint |
| UI carga pero “Connecting…” | `env.json` / `PUBLIC_APP_URL` mal → fija Secret `PUBLIC_APP_URL` |
| Neon `too many connections` | Usa **-pooler** y baja `PG_POOL_MAX` |
| Scripts fallan con `\r` | `.gitattributes` fuerza LF en `*.sh` |
