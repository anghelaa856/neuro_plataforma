# Guía de despliegue — Sistema de Estudio Inteligente

Esta guía te lleva desde cero hasta tener la app pública en **Streamlit Community Cloud** con PostgreSQL en la nube (**Neon.tech** recomendado; también sirve Supabase).

---

## 0. Antes de empezar (checklist)

1. Cuenta en [GitHub](https://github.com)
2. Cuenta en [Neon](https://neon.tech) (o Supabase)
3. Cuenta en [Streamlit Community Cloud](https://share.streamlit.io) (entra con GitHub)
4. Clave de [OpenRouter](https://openrouter.ai/keys)
5. En tu PC: Git instalado (`git --version`)

**Importante:** el archivo `.env` **no** debe subirse a GitHub. El proyecto ya incluye un `.gitignore` que lo excluye.

---

## 1. Crear la base de datos en Neon.tech

1. Entra a [https://console.neon.tech](https://console.neon.tech) y crea un proyecto (región cercana, ej. `US East` o la que prefieras).
2. En el dashboard del proyecto, abre **Dashboard → Connection details** (o **Connect**).
3. Elige conexión tipo **URI** o los campos separados. Anota:

| Dato Neon | Variable en la app |
|-----------|--------------------|
| Host (ej. `ep-xxxx.us-east-2.aws.neon.tech`) | `POSTGRES_HOST` |
| Database (suele ser `neondb`) | `POSTGRES_DB` |
| User (ej. `neondb_owner`) | `POSTGRES_USER` |
| Password | `POSTGRES_PASSWORD` |
| Port (`5432`) | `POSTGRES_PORT` |
| SSL | `POSTGRES_SSLMODE=require` |

4. (Opcional) Copia la **Connection string** completa; también puedes usarla como `DATABASE_URL`:

```text
postgresql://USUARIO:PASSWORD@HOST/neondb?sslmode=require
```

5. No hace falta crear tablas a mano: al arrancar, la app ejecuta `ensure_schema()` y crea/migra `memoria_activa`.

### Si usas Supabase en lugar de Neon

1. Proyecto nuevo en [https://supabase.com](https://supabase.com)
2. **Project Settings → Database**
3. Copia host, database (`postgres`), user (`postgres`), password y puerto
4. Pon siempre `POSTGRES_SSLMODE=require`

---

## 2. Preparar el código en tu PC (Git + GitHub)

Abre PowerShell en la carpeta del proyecto:

```powershell
cd L:\Trabajo\Proyectos\neuro_plataforma
```

### 2.1 Comprobar que `.env` no se va a subir

```powershell
Get-Content .gitignore | Select-String "\.env"
```

Debes ver que `.env` está listado. **No** ejecutes `git add .env`.

### 2.2 Inicializar Git y primer commit

```powershell
git init
git add .
git status
```

Revisa que **NO** aparezca `.env` ni `.venv` en los archivos a commitear.

```powershell
git commit -m "Preparar app para despliegue en Streamlit Cloud con PostgreSQL"
```

### 2.3 Crear el repositorio en GitHub

1. En GitHub: **New repository** (ej. nombre `neuro_plataforma`)
2. **No** marques “Add README” si ya tienes código local
3. Conecta y sube (sustituye `TU_USUARIO` y el nombre del repo):

```powershell
git branch -M main
git remote add origin https://github.com/TU_USUARIO/neuro_plataforma.git
git push -u origin main
```

Si GitHub te pide autenticación, usa un **Personal Access Token** o GitHub CLI (`gh auth login`).

---

## 3. Conectar GitHub con Streamlit Community Cloud

1. Entra a [https://share.streamlit.io](https://share.streamlit.io) e inicia sesión con GitHub.
2. Pulsa **New app** / **Create app**.
3. Completa:
   - **Repository:** `TU_USUARIO/neuro_plataforma`
   - **Branch:** `main`
   - **Main file path:** `app/frontend/main_app.py`
4. (Opcional) Advanced → Python version 3.11 o 3.12.

**Aún no pulses Deploy** hasta pegar los Secrets (paso 4).

---

## 4. Pegar variables en Streamlit → Secrets

1. En la pantalla de creación de la app (o luego en **⋮ → Settings → Secrets**), abre el editor de **Secrets**.
2. Pega un TOML **plano** como este (sustituye valores reales de Neon + OpenRouter):

```toml
APP_ENV = "production"

OPENROUTER_API_KEY = "sk-or-v1-TU_CLAVE_REAL"
OPENROUTER_MODEL = "openrouter/auto"
OPENROUTER_SITE_URL = "https://TU-APP.streamlit.app"
OPENROUTER_APP_NAME = "Neuro Plataforma"

POSTGRES_DB = "neondb"
POSTGRES_USER = "neondb_owner"
POSTGRES_PASSWORD = "TU_PASSWORD_NEON"
POSTGRES_HOST = "ep-xxxx.us-east-2.aws.neon.tech"
POSTGRES_PORT = "5432"
POSTGRES_SSLMODE = "require"

ANOMALY_CONTAMINATION = "0.15"
```

### Alternativa con URL única de Neon

```toml
APP_ENV = "production"
OPENROUTER_API_KEY = "sk-or-v1-TU_CLAVE_REAL"
OPENROUTER_MODEL = "openrouter/auto"
DATABASE_URL = "postgresql://USER:PASSWORD@HOST/neondb?sslmode=require"
```

3. Guarda los Secrets.
4. Pulsa **Deploy**.

La app leerá estos valores: `config/settings.py` usa `os.getenv(...)` y, en Cloud, también copia `st.secrets` hacia el entorno automáticamente.

---

## 5. Verificar que el despliegue funcionó

1. Abre la URL pública que te da Streamlit (`https://....streamlit.app`).
2. Ve a la pestaña **➕ Cargar material**, pega un texto corto y prueba **Extraer tarjetas con IA** (necesita OpenRouter OK).
3. Ve a **📚 Estudiar** y confirma que hay cola / puedes responder.
4. En **📊 Dashboard** deberías ver tarjetas tras guardar.

Si falla la base de datos:

- Revisa `POSTGRES_HOST` (sin `https://`)
- Confirma `POSTGRES_SSLMODE = "require"`
- En Neon, asegúrate de que la IP no esté bloqueada (Neon free suele permitir conexiones externas)

Si falla OpenRouter:

- Revisa la API key en Secrets
- En logs de Streamlit Cloud busca errores SSL/HTTP

---

## 6. Actualizar la app después de cambios

En tu PC:

```powershell
cd L:\Trabajo\Proyectos\neuro_plataforma
git add .
git commit -m "Describe tu cambio"
git push
```

Streamlit Cloud redespliega solo al detectar el push en `main` (si el auto-deploy está activo).

---

## 7. Notas de producción (léelas)

1. **No subas `.env`**. Si alguna vez lo subiste por error, rota (cambia) la API key de OpenRouter y el password de Neon de inmediato.
2. **`psycopg2-binary`** ya está en `requirements.txt` (obligatorio en Linux de Streamlit Cloud).
3. **`certifi`** y **`truststore`** están en `requirements.txt` para SSL estable.
4. **Torch / sentence-transformers** se movieron a `requirements-ml.txt` porque en el plan gratis suelen agotar memoria. En Cloud la app usa fallbacks de NLP (solapamiento de tokens, etc.) y OpenRouter para extracción/tutoría. En tu PC puedes instalar ML completo con:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ml.txt
```

5. Archivo principal de la app en Cloud: **`app/frontend/main_app.py`**.

---

## 8. Resumen rápido de archivos tocados para el despliegue

| Archivo | Rol |
|---------|-----|
| `requirements.txt` | Dependencias Cloud (`psycopg2-binary`, `certifi`, `truststore`) |
| `requirements-ml.txt` | ML pesado solo local |
| `.gitignore` | Excluye `.venv`, `__pycache__`, `.env` |
| `.env.example` | Plantilla sin secretos |
| `config/settings.py` | `os.getenv` + Secrets de Streamlit + SSL Neon |
| `PASO_A_PASO_DESPLIEGUE.md` | Esta guía |

Cuando termines el paso 4 y el deploy quede en verde, tu sistema quedará online **sin necesidad de tener el PC encendido**.
