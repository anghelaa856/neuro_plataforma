---
title: Neuro Plataforma
emoji: 🧠
colorFrom: blue
colorTo: green
sdk: streamlit
sdk_version: "1.44.0"
app_file: app/frontend/main_app.py
pinned: false
---

# Neuro Plataforma — Admisión UNA Biomédicas

Plataforma EdTech de preparación para el examen de admisión a **Medicina (Área Biomédicas, UNA)**.

Incluye: carga de guías personales, práctica enfocada, simulacro oficial (60 Q · 120 min),
tutor socrático y panel **Mi rendimiento** (Índice Medicina).

## Secrets (obligatorio en Spaces)

**Nunca subas tu archivo `.env` al repositorio.**  
Configura las variables en **Settings → Variables and secrets** del Space
(ver `.env.example` como plantilla de nombres).

Mínimo recomendado:

- `DATABASE_URL` **o** `POSTGRES_HOST` + `POSTGRES_DB` + `POSTGRES_USER` + `POSTGRES_PASSWORD` + `POSTGRES_SSLMODE=require`
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL` (opcional; por defecto `openrouter/auto`)
- `OPENROUTER_SITE_URL` (URL pública de tu Space)
- `OPENROUTER_APP_NAME=Neuro Plataforma`
- `APP_ENV=production`

## Ejecutar en local

```bash
pip install -r requirements.txt
streamlit run app/frontend/main_app.py
```
