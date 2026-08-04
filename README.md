---
title: Neuro Plataforma
emoji: 🩺
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Tutor inteligente UNA Puno — Reflex + Neon
---

# Neuro Plataforma — Reflex

EdTech para admisión a **Medicina (UNA Puno)**. App Reflex 0.9.7 + Neon + OpenRouter.

> **Despliegue Hugging Face:** ver guía corta [`HF_DESPLIEGUE.md`](./HF_DESPLIEGUE.md).

## Módulos

| Módulo | Descripción |
|--------|-------------|
| Auth | Login / registro (PBKDF2 + Neon) |
| Mis Guías | Upload PDF/JPG → MCQ privadas (OpenRouter) |
| Práctica | Feedback inmediato + Modo Global + shuffle A–E |
| Simulacro | 60 Q estratificadas (TutorEngine) |
| Banco Admin | Ingesta a bóveda oficial |
| Rendimiento | Índice Medicina + Nivel de Dominio |

## Estructura

```text
neuro_plataforma/          # App Reflex
app/                       # Dominio (repos + TutorEngine + extracción)
config/settings.py
rxconfig.py                # Local LAN / producción HF
Dockerfile                 # One-port: Caddy :7860 + Redis + backend :8000
Caddyfile
scripts/hf_entrypoint.sh
```

## Ejecutar en local

```powershell
cd L:\Trabajo\Proyectos\neuro_plataforma
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Copia .env.example → .env y rellena DATABASE_URL / OPENROUTER_API_KEY
reflex run
```

- App: http://localhost:3005
- Móvil (LAN): http://192.168.100.5:3005

## Ejecutar contenedor (como en HF)

```bash
docker build -t neuro-plataforma:hf .
docker run --rm -p 7860:7860 \
  -e DATABASE_URL="postgresql://…?sslmode=require" \
  -e OPENROUTER_API_KEY="sk-or-…" \
  -e PUBLIC_APP_URL="http://127.0.0.1:7860" \
  neuro-plataforma:hf
```
