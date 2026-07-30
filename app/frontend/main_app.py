import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st

try:
    import extra_streamlit_components as stx
except ImportError:  # pragma: no cover
    stx = None  # type: ignore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database.db_manager import db_manager
from app.infrastructure.database.banco_repository import banco_repository
from app.infrastructure.database.seed_catalogo_materias import seed_catalogo_materias
from app.infrastructure.database.user_repository import user_repository
from app.services.banco_extraction_service import extract_banco_preguntas_from_chunks
from app.services.pdf_processor import process_pdf
from app.services.tutor_engine import (
    BancoInsuficienteError,
    CatalogoInvalidoError,
    ResultadoCierreBloque,
    tutor_engine,
)
from app.services.tutor_socratico_service import (
    TutorContext,
    socratic_tutor_service,
)
from app.frontend.render_student_dashboard_tab import render_student_dashboard_tab
from app.frontend.render_student_ingestion_tab import render_student_ingestion_tab

# Persistencia de sesión en móvil (cookie del navegador)
_COOKIE_USER_SESSION = "user_session"
_COOKIE_TTL_DAYS = 7


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Compat: aduana de limpieza completa; devuelve texto cosido."""
    doc = process_pdf(file_bytes, allow_ocr=True)
    return doc.text


def _get_cookie_manager() -> Any:
    """
    Inicializa CookieManager una vez por script-run con key estable.
    Si falta la librería, devuelve None (login solo con session_state).
    """
    if stx is None:
        return None
    # key fija: evita recrear el componente iframe en cada rerun
    return stx.CookieManager(key="neuro_plataforma_cookie_manager")


def _read_user_session_cookie(cookie_manager: Any) -> Optional[str]:
    if cookie_manager is None:
        return None
    try:
        # Warm-up: en el primer frame get_all suele venir vacío hasta hidratar JS
        all_cookies = cookie_manager.get_all() or {}
        raw = all_cookies.get(_COOKIE_USER_SESSION)
        if raw is None:
            raw = cookie_manager.get(cookie=_COOKIE_USER_SESSION)
        if raw is None:
            return None
        value = str(raw).strip()
        return value or None
    except Exception:
        return None


def _write_user_session_cookie(cookie_manager: Any, email: str) -> None:
    if cookie_manager is None:
        return
    email_n = (email or "").strip().lower()
    if not email_n:
        return
    try:
        expires = datetime.now() + timedelta(days=_COOKIE_TTL_DAYS)
        cookie_manager.set(
            _COOKIE_USER_SESSION,
            email_n,
            key="neuro_set_user_session",
            expires_at=expires,
            path="/",
            same_site="lax",
        )
    except Exception:
        # No bloquear el login si el browser bloquea cookies
        pass


def _delete_user_session_cookie(cookie_manager: Any) -> None:
    if cookie_manager is None:
        return
    try:
        cookie_manager.delete(_COOKIE_USER_SESSION, key="neuro_del_user_session")
    except TypeError:
        # Firmas antiguas de la lib sin `key`
        try:
            cookie_manager.delete(_COOKIE_USER_SESSION)
        except Exception:
            pass
    except Exception:
        pass


def _try_restore_session_from_cookie(cookie_manager: Any) -> bool:
    """
    Si hay cookie user_session y no hay auth_user, reconstruye session_state.
    Retorna True si quedó autenticado.
    """
    if _current_user():
        return True
    if st.session_state.get("_auth_cookie_restore_done"):
        return False

    token = _read_user_session_cookie(cookie_manager)
    if not token:
        # Tras el warmup, si no hay cookie, no insistir en cada rerun.
        if st.session_state.get("_neuro_cookie_warmup"):
            st.session_state["_auth_cookie_restore_done"] = True
        return False

    st.session_state["_auth_cookie_restore_done"] = True
    try:
        # Preferimos email; también aceptamos id numérico por compatibilidad
        user: Optional[Dict[str, Any]] = None
        if token.isdigit():
            user = user_repository.get_by_id(int(token))
        if user is None:
            user = user_repository.get_by_email(token)
        if not user:
            _delete_user_session_cookie(cookie_manager)
            return False
        _establish_user_session(user, cookie_manager=None)  # no reescribir cookie
        return True
    except Exception:
        return False


def init_runtime() -> None:
    defaults: Dict[str, Any] = {
        # Auth multiusuario
        "auth_user": None,
        "usuario_id": None,
        # UNA — Ingesta / Simulacro / Práctica
        "ingesta_last_result": None,
        "simulacro_activo": None,
        "simulacro_index": 0,
        "simulacro_respuestas": {},
        "simulacro_tiempos_ms": {},
        "simulacro_resultado": None,
        "simulacro_cerrado": False,
        "practica_activa": None,
        "practica_index": 0,
        "practica_respuestas": {},
        "practica_tiempos_ms": {},
        "practica_resultado": None,
        "practica_cerrada": False,
        # Active Recall: qid → True tras "Comprobar Respuesta"
        "practica_respuestas__comprobadas": {},
        "practica_respuestas__check_ok": {},
        # Tutor socrático (historial por pregunta; nunca guarda la clave)
        "socratic_chats": {},
        "socratic_justificacion_cache": {},
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    db_manager.connect()
    db_manager.ensure_schema()
    try:
        seed_catalogo_materias(ensure_schema_first=False)
    except Exception:
        # Catálogo puede venir del DDL; no bloquear la UI
        pass


def _current_user() -> Optional[Dict[str, Any]]:
    user = st.session_state.get("auth_user")
    return user if isinstance(user, dict) and user.get("id_usuario") else None


def _require_usuario_id() -> int:
    user = _current_user()
    if not user:
        raise RuntimeError("Debes iniciar sesión.")
    uid = int(user["id_usuario"])
    st.session_state["usuario_id"] = uid
    return uid


def _effective_usuario_id() -> int:
    """
    Usuario autenticado si hay sesión; si no, fallback a 1 (modo demo HF).
    """
    user = _current_user()
    if user and user.get("id_usuario"):
        uid = int(user["id_usuario"])
        st.session_state["usuario_id"] = uid
        return uid
    st.session_state["usuario_id"] = 1
    return 1


def _establish_user_session(
    auth: Dict[str, Any],
    *,
    cookie_manager: Any = None,
) -> None:
    """Guarda el usuario en session_state y limpia estado de bloque UNA ajeno."""
    uid = int(auth["id_usuario"])
    st.session_state["auth_user"] = {
        "id_usuario": uid,
        "email": auth["email"],
        "nombre": auth["nombre"],
    }
    st.session_state["usuario_id"] = uid
    # Limpia bloques MCQ / tutor de la sesión previa
    st.session_state["simulacro_activo"] = None
    st.session_state["practica_activa"] = None
    st.session_state["practica_respuestas__comprobadas"] = {}
    st.session_state["practica_respuestas__check_ok"] = {}
    st.session_state["socratic_chats"] = {}
    if cookie_manager is not None:
        _write_user_session_cookie(cookie_manager, str(auth.get("email") or ""))


def _logout(*, cookie_manager: Any = None) -> None:
    st.session_state["auth_user"] = None
    st.session_state["usuario_id"] = None
    st.session_state["simulacro_activo"] = None
    st.session_state["practica_activa"] = None
    st.session_state["practica_respuestas__comprobadas"] = {}
    st.session_state["practica_respuestas__check_ok"] = {}
    st.session_state["socratic_chats"] = {}
    st.session_state["_auth_cookie_restore_done"] = True
    _delete_user_session_cookie(cookie_manager)


def render_auth_gate(*, cookie_manager: Any = None) -> bool:
    """
    Pantalla de Login / Registro.
    Retorna True si hay sesión activa; False si debe detener el render.
    """
    # Cookie → session_state (sobrevive a background en móvil)
    if _try_restore_session_from_cookie(cookie_manager):
        return True

    user = _current_user()
    if user:
        return True

    st.title("🧠 Sistema de Estudio Inteligente")
    st.caption(
        "Inicia sesión o crea una cuenta para guardar tu progreso. "
        "En el celular, tu sesión se recuerda hasta 7 días."
    )

    tab_login, tab_register = st.tabs(["Iniciar sesión", "Registrarse"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Contraseña", type="password", key="login_password")
            submitted = st.form_submit_button("Entrar", type="primary", use_container_width=True)
        if submitted:
            try:
                auth = user_repository.authenticate(email=email, password=password)
                if not auth:
                    st.error("Email o contraseña incorrectos.")
                else:
                    _establish_user_session(auth, cookie_manager=cookie_manager)
                    st.success(f"Bienvenido/a, {auth['nombre']}.")
                    st.rerun()
            except Exception as exc:
                st.error(f"No se pudo iniciar sesión: {exc}")

    with tab_register:
        with st.form("register_form"):
            nombre = st.text_input("Nombre", key="reg_nombre")
            email = st.text_input("Email", key="reg_email")
            password = st.text_input("Contraseña (mín. 6)", type="password", key="reg_password")
            password2 = st.text_input("Repetir contraseña", type="password", key="reg_password2")
            submitted = st.form_submit_button("Crear cuenta", type="primary", use_container_width=True)
        if submitted:
            if password != password2:
                st.error("Las contraseñas no coinciden.")
            else:
                try:
                    created = user_repository.create_user(
                        email=email,
                        password=password,
                        nombre=nombre,
                    )
                    _establish_user_session(created, cookie_manager=cookie_manager)
                    st.success("Cuenta creada. Ya estás dentro.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"No se pudo registrar: {exc}")

    return False


def build_ui() -> None:
    st.set_page_config(
        page_title="Neuro Plataforma · Admisión UNA",
        page_icon="🧠",
        layout="wide",
    )

    # CookieManager lo antes posible (tras page_config)
    cookie_manager = _get_cookie_manager()
    if cookie_manager is not None and not st.session_state.get("_neuro_cookie_warmup"):
        # Primer frame: forzar hidratación JS de cookies y re-ejecutar
        try:
            cookie_manager.get_all()
        except Exception:
            pass
        st.session_state["_neuro_cookie_warmup"] = True
        st.rerun()

    if not render_auth_gate(cookie_manager=cookie_manager):
        return

    user = _current_user() or {}
    top_l, top_r = st.columns([3, 1])
    with top_l:
        st.title("🧠 Neuro Plataforma — Admisión UNA Biomédicas")
        st.caption(
            f"Sesión: **{user.get('nombre', '')}** ({user.get('email', '')}) · "
            "TutorEngine · bóveda Neon · OpenRouter"
        )
    with top_r:
        st.write("")
        if st.button("Cerrar sesión", use_container_width=True):
            _logout(cookie_manager=cookie_manager)
            st.rerun()

    tab_guias, tab_practica, tab_simulacro, tab_ingesta, tab_dashboard = st.tabs(
        [
            "📚 Mis Guías",
            "🎯 Práctica Enfocada",
            "📝 Simulacro Oficial UNA (120 min)",
            "📥 Banco Oficial (Admin)",
            "📊 Mi rendimiento",
        ]
    )
    with tab_guias:
        render_student_ingestion_tab(usuario_id=_effective_usuario_id())
    with tab_practica:
        render_practica_enfocada_tab()
    with tab_simulacro:
        render_simulacro_oficial_tab()
    with tab_ingesta:
        render_ingesta_admin_tab()
    with tab_dashboard:
        render_student_dashboard_tab(
            usuario_id=_effective_usuario_id(),
            nombre_alumno=str(user.get("nombre") or ""),
        )


# ---------------------------------------------------------------------------
# Producción UNA — Pestaña 1: Ingesta Admin
# ---------------------------------------------------------------------------
def render_ingesta_admin_tab() -> None:
    st.subheader("📥 Banco Oficial (Admin)")
    st.caption(
        "PDF → aduana → OpenRouter (MCQ A–E) → bóveda **oficial** "
        "(`propietario_usuario_id` NULL). Visible para todos los alumnos."
    )

    try:
        materias = banco_repository.fetch_materias()
    except Exception as exc:
        st.error(f"No se pudo leer catalogo_materias: {exc}")
        return

    if not materias:
        st.warning(
            "El catálogo está vacío. Ejecuta el seed Tabla 4 Biomédicas "
            "(`python -m scripts.bootstrap_admision_neon`)."
        )
        return

    labels = {
        int(m["id_materia"]): (
            f"{int(m['codigo']):02d} · {m['nombre']} "
            f"({int(m['cantidad_preguntas'])} Q · factor {m['factor_ponderacion']})"
        )
        for m in materias
    }
    materia_id = st.selectbox(
        "Materia oficial (catalogo_materias)",
        options=list(labels.keys()),
        format_func=lambda mid: labels[mid],
        key="ingesta_materia_id",
    )

    uploaded = st.file_uploader(
        "PDF de academia / apuntes",
        type=["pdf"],
        key="ingesta_pdf_uploader",
    )
    max_items = st.slider("Ítems máx. por fragmento", 1, 8, 5, key="ingesta_max_items")

    if st.button(
        "Procesar y Guardar en Bóveda",
        type="primary",
        disabled=uploaded is None,
        use_container_width=True,
    ):
        if uploaded is None:
            st.error("Sube un PDF primero.")
            return
        try:
            with st.spinner("Aduana PDF (extracción / OCR / limpieza / chunks)..."):
                doc = process_pdf(
                    uploaded.getvalue(),
                    allow_ocr=True,
                    source_filename=getattr(uploaded, "name", None),
                )
            if not doc.text or not doc.chunks:
                st.error("PDF sin texto usable. ¿Escaneado sin OCR disponible?")
                for w in doc.warnings:
                    st.caption(f"⚠️ {w}")
                return

            nombre_archivo = (
                (doc.meta or {}).get("nombre_archivo_fuente")
                or getattr(uploaded, "name", None)
                or "desconocido.pdf"
            )
            st.info(
                f"PDF OK · archivo=`{nombre_archivo}` · método=`{doc.method}` · "
                f"{doc.page_count} págs · {len(doc.chunks)} chunks · "
                f"{doc.chars_per_page:.0f} chars/pág"
            )
            for w in doc.warnings:
                st.caption(f"⚠️ {w}")

            with st.spinner("OpenRouter generando ítems MCQ e insertando en Neon..."):
                result = extract_banco_preguntas_from_chunks(
                    doc.chunks,
                    materia_id=int(materia_id),
                    auto_clasificar=False,
                    max_items_per_chunk=int(max_items),
                    origen_contenido="pdf",
                    fuente="openrouter",
                    nombre_archivo_fuente=str(nombre_archivo),
                    propietario_usuario_id=None,
                    persist=True,
                )
            st.session_state["ingesta_last_result"] = result.to_dict()
            pers = result.persistencia or {}
            st.success(
                f"Bóveda actualizada · materia **{result.materia_nombre}** · "
                f"archivo=`{pers.get('nombre_archivo_fuente') or nombre_archivo}` · "
                f"validados={result.items_validados} · "
                f"insertadas={pers.get('n_insertadas', 0)} · "
                f"duplicadas={pers.get('n_duplicadas', 0)} · "
                f"temas={pers.get('temas_upserted', 0)}"
            )
            if result.warnings:
                with st.expander("Avisos de extracción"):
                    for w in result.warnings:
                        st.write(f"- {w}")
        except Exception as exc:
            st.error(f"Fallo en el pipeline de ingesta: {exc}")

    last = st.session_state.get("ingesta_last_result")
    if last:
        with st.expander("Último resultado (detalle)"):
            st.json(last)


# ---------------------------------------------------------------------------
# Producción UNA — Pestaña 2: Simulacro Oficial
# ---------------------------------------------------------------------------
def _accumulate_mcq_time(
    *,
    tiempos_key: str,
    timer_qid_key: str,
    timer_started_key: str,
) -> None:
    """Suma el tiempo transcurrido de la pregunta activa al dict de tiempos (ms)."""
    started = st.session_state.get(timer_started_key)
    qid = st.session_state.get(timer_qid_key)
    if started is None or qid is None:
        return
    elapsed_ms = max(0, int((time.time() - float(started)) * 1000))
    tiempos: Dict[str, int] = dict(st.session_state.get(tiempos_key) or {})
    tiempos[str(qid)] = int(tiempos.get(str(qid), 0)) + elapsed_ms
    st.session_state[tiempos_key] = tiempos
    st.session_state[timer_started_key] = None


def _ensure_mcq_question_timer(
    *,
    pregunta_id: str,
    tiempos_key: str,
    timer_qid_key: str,
    timer_started_key: str,
) -> None:
    """Al cambiar de ítem, acumula el anterior y arranca cronómetro del actual."""
    current = str(pregunta_id)
    if st.session_state.get(timer_qid_key) != current:
        _accumulate_mcq_time(
            tiempos_key=tiempos_key,
            timer_qid_key=timer_qid_key,
            timer_started_key=timer_started_key,
        )
        st.session_state[timer_qid_key] = current
        st.session_state[timer_started_key] = time.time()


def _render_cierre_resultado(resultado: ResultadoCierreBloque) -> None:
    """Resumen post-finalización (ledger ya persistido)."""
    st.success(
        f"Guardado en `historial_intentos` · {resultado.n_insertados} intentos · "
        f"correctas={resultado.correctas} · incorrectas={resultado.incorrectas} · "
        f"en blanco={resultado.en_blanco}"
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("Puntaje bruto", f"{resultado.puntaje_bruto:.0f}")
    c2.metric("Puntaje ponderado", f"{resultado.puntaje_ponderado:.3f}")
    c3.metric("Tiempo total", f"{resultado.tiempo_total_ms / 1000:.1f} s")
    if resultado.id_sesion:
        st.caption(f"Sesión simulacro `{resultado.id_sesion}` marcada como **finalizada**.")
    with st.expander("Detalle por pregunta"):
        st.dataframe(resultado.detalle_por_pregunta, use_container_width=True)


def _clear_socratic_chats(*, modo_origen: Optional[str] = None) -> None:
    """Limpia historiales de chat (todo o solo un modo)."""
    chats: Dict[str, Any] = dict(st.session_state.get("socratic_chats") or {})
    if modo_origen is None:
        st.session_state["socratic_chats"] = {}
        return
    prefix = f"{modo_origen}:"
    st.session_state["socratic_chats"] = {
        k: v for k, v in chats.items() if not str(k).startswith(prefix)
    }


def _socratic_chat_storage_key(modo_origen: str, pregunta_id: int) -> str:
    return f"{modo_origen}:{int(pregunta_id)}"


def _resolve_justificacion_banco(pregunta_id: int) -> Optional[str]:
    """Lee justificación del banco con caché de sesión (no expone la clave)."""
    cache: Dict[str, Any] = dict(st.session_state.get("socratic_justificacion_cache") or {})
    key = str(int(pregunta_id))
    if key in cache:
        val = cache[key]
        return str(val) if val else None
    try:
        just = banco_repository.fetch_justificacion(int(pregunta_id))
    except Exception:
        just = None
    cache[key] = just or ""
    st.session_state["socratic_justificacion_cache"] = cache
    return just


def _build_tutor_context_server_side(
    *,
    pregunta: Any,
    answers: Dict[str, str],
    modo_origen: str,
    sesion_id: Optional[int],
) -> TutorContext:
    """
    Arma TutorContext en servidor.

    La alternativa_correcta sale del objeto PreguntaTutor en memoria
    (sesión Streamlit del proceso), NUNCA de un widget/público del cliente.
    """
    qid = int(getattr(pregunta, "id_pregunta"))
    alts = getattr(pregunta, "alternativas", {}) or {}
    if not isinstance(alts, dict):
        alts = {}
    correcta = str(getattr(pregunta, "alternativa_correcta", "") or "").strip().upper()
    marcada = answers.get(str(qid)) or answers.get(qid)  # type: ignore[index]
    materia = str(getattr(pregunta, "materia_nombre", "") or "").strip()
    tema = str(getattr(pregunta, "tema_nombre", "") or "").strip()
    tema_o_materia = " · ".join(p for p in (materia, tema) if p) or "Sin tema"

    try:
        uid = _effective_usuario_id()
    except Exception:
        uid = None

    return TutorContext(
        pregunta_id=qid,
        enunciado_pregunta=str(getattr(pregunta, "enunciado", "") or ""),
        alternativas={str(k).upper(): str(v) for k, v in alts.items()},
        alternativa_correcta=correcta,
        tema_o_materia=tema_o_materia,
        sesion_id=int(sesion_id) if sesion_id is not None else None,
        alternativa_marcada_por_alumno=str(marcada).upper() if marcada else None,
        justificacion_banco=_resolve_justificacion_banco(qid),
        modo_origen=modo_origen,
        usuario_id=int(uid) if uid is not None else None,
    )


def _render_socratic_tutor_panel(
    *,
    pregunta: Any,
    answers_key: str,
    modo_origen: str,
    sesion_id: Optional[int] = None,
    panel_title: str = "💬 Tengo una duda (tutor socrático)",
    expanded: bool = False,
) -> None:
    """
    Chat opt-in encapsulado por pregunta_id.

    No muta respuestas MCQ ni tiempos; no escribe en historial_intentos.
    """
    qid = int(getattr(pregunta, "id_pregunta"))
    storage_key = _socratic_chat_storage_key(modo_origen, qid)
    chats: Dict[str, List[Dict[str, str]]] = dict(
        st.session_state.get("socratic_chats") or {}
    )
    historial: List[Dict[str, str]] = list(chats.get(storage_key) or [])
    open_panel = bool(expanded) or bool(historial)

    with st.expander(panel_title, expanded=open_panel):
        st.caption(
            "El tutor guía sin revelar la letra correcta. "
            "Abrir el chat **no** cambia tu marca ni el puntaje."
        )
        for turn in historial:
            role = turn.get("role") or "assistant"
            if role not in {"user", "assistant"}:
                role = "assistant"
            with st.chat_message(role):
                st.markdown(turn.get("content") or "")

        # text_input + botón (chat_input no es fiable dentro de expanders)
        draft_n_key = f"socratic_draft_n::{storage_key}"
        draft_n = int(st.session_state.get(draft_n_key) or 0)
        input_key = f"socratic_draft::{storage_key}::{draft_n}"
        col_in, col_btn = st.columns([4, 1])
        with col_in:
            draft = st.text_input(
                "Tu duda",
                key=input_key,
                placeholder="Ej. ¿Por qué mi opción no encaja con el enunciado?",
                label_visibility="collapsed",
            )
        with col_btn:
            enviar = st.button(
                "Enviar",
                type="primary",
                use_container_width=True,
                key=f"socratic_send::{storage_key}",
            )

        if not enviar:
            return

        mensaje = str(draft or "").strip()
        if not mensaje:
            st.warning("Escribe una duda antes de enviar.")
            return

        answers: Dict[str, str] = dict(st.session_state.get(answers_key) or {})
        # Preserva respuestas antes de cualquier trabajo de red.
        st.session_state[answers_key] = answers

        historial.append({"role": "user", "content": mensaje})
        chats[storage_key] = historial
        st.session_state["socratic_chats"] = chats
        # Nueva key de input en el próximo rerun (no mutar widget ya montado)
        st.session_state[draft_n_key] = draft_n + 1

        try:
            contexto = _build_tutor_context_server_side(
                pregunta=pregunta,
                answers=answers,
                modo_origen=modo_origen,
                sesion_id=sesion_id,
            )
            with st.spinner("Tutor pensando (socrático)…"):
                reply = socratic_tutor_service.generar_respuesta_socratica(
                    contexto,
                    mensaje,
                    historial_chat=historial[:-1],
                )
            assistant_text = reply.texto
            if reply.spoilers_bloqueados:
                assistant_text += (
                    "\n\n_*(respuesta filtrada: el modelo intentó revelar la clave)*_"
                )
        except Exception as exc:
            assistant_text = (
                "No pude consultar al tutor en este momento. "
                f"Intenta de nuevo. ({type(exc).__name__})"
            )

        historial.append({"role": "assistant", "content": assistant_text})
        chats[storage_key] = historial
        st.session_state["socratic_chats"] = chats
        st.session_state[answers_key] = answers
        st.rerun()


def _render_mcq_review_with_tutor(
    *,
    preguntas: List[Any],
    index_key: str,
    answers_key: str,
    modo_origen: str,
    sesion_id: Optional[int] = None,
) -> None:
    """Navegación post-cierre: revisa ítems + tutor socrático opt-in."""
    total = len(preguntas)
    if total == 0:
        return

    review_index_key = f"{index_key}__review"
    idx = int(st.session_state.get(review_index_key) or 0)
    idx = max(0, min(idx, total - 1))
    st.session_state[review_index_key] = idx

    answers: Dict[str, str] = dict(st.session_state.get(answers_key) or {})
    q = preguntas[idx]
    orden = getattr(q, "orden", idx + 1)
    materia = getattr(q, "materia_nombre", "")
    tema = getattr(q, "tema_nombre", "")
    enunciado = getattr(q, "enunciado", "")
    alts = getattr(q, "alternativas", {}) or {}
    if not isinstance(alts, dict):
        alts = {}
    qid = str(getattr(q, "id_pregunta", orden))
    marcada = answers.get(qid)

    st.subheader("🔍 Revisión con tutor")
    st.caption(
        "Modo revisión post-evaluación. Puedes pedir ayuda socrática por ítem. "
        "La clave correcta no se muestra aquí; solo la usa el tutor en servidor."
    )
    st.progress((idx + 1) / total, text=f"Revisión: {idx + 1} / {total}")
    st.markdown(f"**#{orden}** · {materia} · _{tema}_")
    st.markdown(f"### {enunciado}")

    for k in ("A", "B", "C", "D", "E"):
        if k not in alts:
            continue
        marker = " ← tu marca" if marcada == k else ""
        st.markdown(f"- **{k})** {alts[k]}{marker}")

    if marcada:
        st.info(f"Tu respuesta registrada: **{marcada}**")
    else:
        st.warning("Este ítem quedó **en blanco**.")

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button(
            "← Anterior",
            disabled=idx <= 0,
            use_container_width=True,
            key=f"review_prev_{answers_key}",
        ):
            st.session_state[review_index_key] = idx - 1
            st.rerun()
    with c2:
        st.caption(f"Ítem {idx + 1} de {total}")
    with c3:
        if st.button(
            "Siguiente →",
            disabled=idx >= total - 1,
            use_container_width=True,
            key=f"review_next_{answers_key}",
        ):
            st.session_state[review_index_key] = idx + 1
            st.rerun()

    _render_socratic_tutor_panel(
        pregunta=q,
        answers_key=answers_key,
        modo_origen=modo_origen,
        sesion_id=sesion_id,
    )


def _render_mcq_navigator(
    *,
    preguntas: List[Any],
    index_key: str,
    answers_key: str,
    tiempos_key: str,
    titulo_progreso: str,
    finalize_label: str = "Finalizar y Evaluar",
    on_finalize: Any = None,
    cerrado_key: Optional[str] = None,
    resultado_key: Optional[str] = None,
    modo_origen: str = "practica",
    sesion_id: Optional[int] = None,
    tutor_en_vivo: bool = False,
    feedback_inmediato: bool = False,
) -> None:
    """Render genérico de ítems A–E con navegación, timer y cierre de ciclo."""
    total = len(preguntas)
    if total == 0:
        st.warning("No hay preguntas para mostrar.")
        return

    if cerrado_key and st.session_state.get(cerrado_key):
        resultado = st.session_state.get(resultado_key) if resultado_key else None
        if isinstance(resultado, ResultadoCierreBloque):
            _render_cierre_resultado(resultado)
        else:
            st.info("Este bloque ya fue finalizado y guardado.")
        st.divider()
        _render_mcq_review_with_tutor(
            preguntas=preguntas,
            index_key=index_key,
            answers_key=answers_key,
            modo_origen=modo_origen,
            sesion_id=sesion_id,
        )
        return

    idx = int(st.session_state.get(index_key) or 0)
    idx = max(0, min(idx, total - 1))
    st.session_state[index_key] = idx
    answers: Dict[str, str] = dict(st.session_state.get(answers_key) or {})

    checked_key = f"{answers_key}__comprobadas"
    check_ok_key = f"{answers_key}__check_ok"
    comprobadas: Dict[str, bool] = dict(st.session_state.get(checked_key) or {})
    check_ok: Dict[str, bool] = dict(st.session_state.get(check_ok_key) or {})

    timer_qid_key = f"{answers_key}__timer_qid"
    timer_started_key = f"{answers_key}__timer_started"

    st.progress((idx + 1) / total, text=f"{titulo_progreso}: {idx + 1} / {total}")
    q = preguntas[idx]
    orden = getattr(q, "orden", idx + 1)
    materia = getattr(q, "materia_nombre", "")
    tema = getattr(q, "tema_nombre", "")
    enunciado = getattr(q, "enunciado", "")
    alts = getattr(q, "alternativas", {}) or {}
    if not isinstance(alts, dict):
        alts = {}

    qid = str(getattr(q, "id_pregunta", orden))
    _ensure_mcq_question_timer(
        pregunta_id=qid,
        tiempos_key=tiempos_key,
        timer_qid_key=timer_qid_key,
        timer_started_key=timer_started_key,
    )

    tiempos_now: Dict[str, int] = dict(st.session_state.get(tiempos_key) or {})
    started = st.session_state.get(timer_started_key)
    live_ms = int(tiempos_now.get(qid, 0))
    if started is not None:
        live_ms += max(0, int((time.time() - float(started)) * 1000))
    st.caption(f"⏱ Tiempo en esta pregunta: **{live_ms / 1000:.1f} s**")

    st.markdown(f"**#{orden}** · {materia} · _{tema}_")
    st.markdown(f"### {enunciado}")

    opciones = [k for k in ("A", "B", "C", "D", "E") if k in alts]
    current = answers.get(qid)
    default_i = opciones.index(current) if current in opciones else 0
    ya_comprobada = bool(feedback_inmediato and comprobadas.get(qid))

    radio_kwargs: Dict[str, Any] = {
        "options": opciones,
        "format_func": lambda k: f"{k}) {alts.get(k, '')}",
        "index": default_i if opciones else 0,
        "key": f"mcq_radio_{answers_key}_{qid}_{idx}",
    }
    if feedback_inmediato:
        radio_kwargs["disabled"] = ya_comprobada

    choice = st.radio("Tu respuesta", **radio_kwargs)
    if not ya_comprobada:
        answers[qid] = choice
        st.session_state[answers_key] = answers
    else:
        # Mantén la marca congelada al comprobar
        choice = answers.get(qid) or choice
        st.session_state[answers_key] = answers

    fallo_actual = False
    if feedback_inmediato:
        if not ya_comprobada:
            if st.button(
                "Comprobar Respuesta",
                type="primary",
                use_container_width=True,
                key=f"btn_check_{answers_key}_{qid}",
            ):
                marcada = str(answers.get(qid) or choice or "").strip().upper()
                correcta = str(getattr(q, "alternativa_correcta", "") or "").strip().upper()
                es_ok = bool(marcada) and marcada == correcta
                comprobadas[qid] = True
                check_ok[qid] = es_ok
                st.session_state[checked_key] = comprobadas
                st.session_state[check_ok_key] = check_ok
                st.session_state[answers_key] = answers
                st.rerun()
        else:
            es_ok = bool(check_ok.get(qid))
            fallo_actual = not es_ok
            if es_ok:
                st.success("¡Correcto!")
            else:
                st.error(
                    "Respuesta incorrecta. ¡Usa el tutor socrático para entender por qué!"
                )
                st.info(
                    "👇 Abre el tutor abajo: te guía con preguntas, **sin** revelar la letra."
                )

    puede_avanzar = (not feedback_inmediato) or ya_comprobada

    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("← Anterior", disabled=idx <= 0, use_container_width=True):
            st.session_state[index_key] = idx - 1
            st.rerun()
    with c2:
        st.caption(f"Marcadas: {len(answers)} / {total}")
        if feedback_inmediato and not ya_comprobada:
            st.caption("Comprueba tu respuesta para continuar →")
    with c3:
        if st.button(
            "Siguiente →",
            disabled=(idx >= total - 1) or (not puede_avanzar),
            use_container_width=True,
            type="primary" if idx < total - 1 else "secondary",
            key=f"btn_next_{answers_key}_{idx}",
        ):
            st.session_state[index_key] = idx + 1
            st.rerun()

    if tutor_en_vivo:
        if feedback_inmediato and not ya_comprobada:
            st.caption("Tutor socrático disponible **después** de comprobar tu respuesta.")
        else:
            _render_socratic_tutor_panel(
                pregunta=q,
                answers_key=answers_key,
                modo_origen=modo_origen,
                sesion_id=sesion_id,
                expanded=fallo_actual,
                panel_title=(
                    "💬 Tutor socrático — ¡úsalo para entender el error!"
                    if fallo_actual
                    else "💬 Tengo una duda (tutor socrático)"
                ),
            )
    else:
        st.caption(
            "Tutor socrático disponible en **revisión** al finalizar el bloque "
            "(integridad del cronómetro)."
        )

    # Cierre del ciclo solo en la última pregunta
    if idx >= total - 1 and callable(on_finalize):
        st.divider()
        pendientes = total - len(answers)
        if pendientes > 0:
            st.warning(
                f"Hay {pendientes} pregunta(s) sin marcar. "
                "Se registrarán como **en blanco** (+2 pts base UNA)."
            )
        puede_finalizar = (not feedback_inmediato) or ya_comprobada
        if st.button(
            finalize_label,
            type="primary",
            use_container_width=True,
            key=f"btn_fin_{answers_key}",
            disabled=not puede_finalizar,
        ):
            _accumulate_mcq_time(
                tiempos_key=tiempos_key,
                timer_qid_key=timer_qid_key,
                timer_started_key=timer_started_key,
            )
            try:
                with st.spinner("Evaluando y guardando en el ledger..."):
                    resultado = on_finalize(
                        dict(st.session_state.get(answers_key) or {}),
                        dict(st.session_state.get(tiempos_key) or {}),
                    )
                if cerrado_key:
                    st.session_state[cerrado_key] = True
                if resultado_key:
                    st.session_state[resultado_key] = resultado
                st.rerun()
            except Exception as exc:
                st.error(f"No se pudo finalizar: {exc}")


def render_simulacro_oficial_tab() -> None:
    st.subheader("📝 Simulacro Oficial UNA (60 preguntas · 120 min)")
    st.caption(
        "Muestreo estratificado por `catalogo_materias` + priorización de debilidades "
        "desde `historial_intentos`."
    )

    uid = _effective_usuario_id()
    st.caption(f"usuario_id efectivo: `{uid}`")

    fuente_labels = {
        "todo": "Todo (oficial + mis guías)",
        "oficial": "Solo Banco Oficial",
        "mis_guias": "Solo Mis Guías",
    }
    fuente_banco = st.radio(
        "Fuente de preguntas",
        options=list(fuente_labels.keys()),
        format_func=lambda k: fuente_labels[k],
        horizontal=True,
        key="simulacro_fuente_banco",
    )

    col_a, col_b = st.columns([2, 1])
    with col_a:
        start = st.button("Iniciar Simulacro Oficial", type="primary", use_container_width=True)
    with col_b:
        if st.button("Reiniciar simulacro", use_container_width=True):
            st.session_state["simulacro_activo"] = None
            st.session_state["simulacro_index"] = 0
            st.session_state["simulacro_respuestas"] = {}
            st.session_state["simulacro_tiempos_ms"] = {}
            st.session_state["simulacro_resultado"] = None
            st.session_state["simulacro_cerrado"] = False
            st.session_state["simulacro_index__review"] = 0
            _clear_socratic_chats(modo_origen="simulacro")
            st.rerun()

    if start:
        try:
            with st.spinner("TutorEngine generando examen de 60 ítems..."):
                examen = tutor_engine.generar_simulacro_oficial(
                    usuario_id=uid,
                    persistir_sesion=True,
                    fuente_banco=str(fuente_banco),
                )
            st.session_state["simulacro_activo"] = examen
            st.session_state["simulacro_index"] = 0
            st.session_state["simulacro_respuestas"] = {}
            st.session_state["simulacro_tiempos_ms"] = {}
            st.session_state["simulacro_resultado"] = None
            st.session_state["simulacro_cerrado"] = False
            st.session_state["simulacro_index__review"] = 0
            _clear_socratic_chats(modo_origen="simulacro")
            st.success(
                f"Simulacro listo · sesión `{examen.id_sesion}` · "
                f"techo ponderado {examen.puntaje_maximo_ponderado} · "
                f"seed `{examen.seed_muestreo}`"
            )
            st.rerun()
        except (BancoInsuficienteError, CatalogoInvalidoError) as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"No se pudo generar el simulacro: {exc}")

    examen = st.session_state.get("simulacro_activo")
    if not examen:
        st.info("Pulsa **Iniciar Simulacro Oficial** cuando el banco tenga cupo por materia.")
        return

    with st.expander("Composición por materia (prospecto)"):
        st.dataframe(examen.composicion_por_materia, use_container_width=True)

    def _on_finalize_simulacro(respuestas: Dict[str, str], tiempos: Dict[str, int]):
        return tutor_engine.finalizar_simulacro_oficial(
            simulacro=examen,
            respuestas=respuestas,
            tiempos_ms=tiempos,
        )

    _render_mcq_navigator(
        preguntas=list(examen.preguntas),
        index_key="simulacro_index",
        answers_key="simulacro_respuestas",
        tiempos_key="simulacro_tiempos_ms",
        titulo_progreso="Simulacro",
        finalize_label="Finalizar y Evaluar simulacro",
        on_finalize=_on_finalize_simulacro,
        cerrado_key="simulacro_cerrado",
        resultado_key="simulacro_resultado",
        modo_origen="simulacro",
        sesion_id=int(examen.id_sesion) if getattr(examen, "id_sesion", None) else None,
        tutor_en_vivo=False,
    )


# ---------------------------------------------------------------------------
# Producción UNA — Pestaña 3: Práctica Enfocada
# ---------------------------------------------------------------------------
def _reset_practica_session_state() -> None:
    st.session_state["practica_activa"] = None
    st.session_state["practica_index"] = 0
    st.session_state["practica_respuestas"] = {}
    st.session_state["practica_tiempos_ms"] = {}
    st.session_state["practica_resultado"] = None
    st.session_state["practica_cerrada"] = False
    st.session_state["practica_index__review"] = 0
    st.session_state["practica_respuestas__comprobadas"] = {}
    st.session_state["practica_respuestas__check_ok"] = {}
    _clear_socratic_chats(modo_origen="practica")


def _activate_practica_bloque(bloque: Any) -> None:
    st.session_state["practica_activa"] = bloque
    st.session_state["practica_index"] = 0
    st.session_state["practica_respuestas"] = {}
    st.session_state["practica_tiempos_ms"] = {}
    st.session_state["practica_resultado"] = None
    st.session_state["practica_cerrada"] = False
    st.session_state["practica_index__review"] = 0
    st.session_state["practica_respuestas__comprobadas"] = {}
    st.session_state["practica_respuestas__check_ok"] = {}
    _clear_socratic_chats(modo_origen="practica")


def render_practica_enfocada_tab() -> None:
    st.subheader("🎯 Práctica Enfocada")
    st.caption(
        "Bloque libre (SRS + tasa de error) sin cupos del prospecto. "
        "Al finalizar, los intentos van a `historial_intentos` con `sesion_id = NULL`. "
        "Feedback inmediato: comprueba cada respuesta antes de avanzar."
    )

    uid = _effective_usuario_id()
    try:
        materias = banco_repository.fetch_materias()
    except Exception as exc:
        st.error(f"No se pudo leer catalogo_materias: {exc}")
        return

    if not materias:
        st.warning("Catálogo vacío. Siembra Tabla 4 Biomédicas primero.")
        return

    fuente_labels = {
        "todo": "Todo (oficial + mis guías)",
        "oficial": "Solo Banco Oficial",
        "mis_guias": "Solo Mis Guías",
    }
    fuente_banco = st.radio(
        "Fuente de preguntas",
        options=list(fuente_labels.keys()),
        format_func=lambda k: fuente_labels[k],
        horizontal=True,
        key="practica_fuente_banco",
    )

    st.markdown("#### Práctica inteligente")
    st.caption(
        "Salta los selectores: arma 10–15 preguntas mezclando tus temas con peor precisión."
    )
    if st.button(
        "🧠 Generar Práctica de mis Debilidades",
        type="primary",
        use_container_width=True,
        key="practica_btn_debilidades",
    ):
        try:
            with st.spinner("Consultando tu historial y armando práctica inteligente..."):
                bloque = tutor_engine.generar_practica_debilidades(
                    usuario_id=uid,
                    limite=12,
                    top_temas=5,
                    min_intentos=2,
                    fuente_banco=str(fuente_banco),
                )
            _activate_practica_bloque(bloque)
            temas_n = (bloque.resumen_prioridad or {}).get("temas_debiles", "?")
            st.success(
                f"Práctica de debilidades lista · {bloque.total_preguntas} ítems "
                f"desde {temas_n} tema(s) débiles · prioridad={bloque.resumen_prioridad}"
            )
            st.rerun()
        except (BancoInsuficienteError, ValueError) as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"No se pudo generar la práctica inteligente: {exc}")

    st.divider()
    st.markdown("#### O elige materia / tema manualmente")

    labels = {
        int(m["id_materia"]): f"{int(m['codigo']):02d} · {m['nombre']}" for m in materias
    }
    materia_id = st.selectbox(
        "Materia",
        options=list(labels.keys()),
        format_func=lambda mid: labels[mid],
        key="practica_materia_id",
    )

    try:
        temas = banco_repository.fetch_temas_by_materia(int(materia_id))
    except Exception:
        temas = []

    tema_options: Dict[Optional[int], str] = {None: "(Toda la materia)"}
    for t in temas:
        tema_options[int(t["id_tema"])] = str(t["nombre"])

    tema_id = st.selectbox(
        "Tema",
        options=list(tema_options.keys()),
        format_func=lambda tid: tema_options[tid],
        key="practica_tema_id",
    )
    limite = st.slider("Cantidad de preguntas", 5, 30, 15, key="practica_limite")

    c1, c2 = st.columns(2)
    with c1:
        generar = st.button("Generar Práctica", type="secondary", use_container_width=True)
    with c2:
        if st.button("Limpiar práctica", use_container_width=True):
            _reset_practica_session_state()
            st.rerun()

    if generar:
        try:
            with st.spinner("TutorEngine armando bloque de práctica..."):
                bloque = tutor_engine.generar_practica_enfocada(
                    usuario_id=uid,
                    materia_id=int(materia_id),
                    tema_id=int(tema_id) if tema_id is not None else None,
                    limite=int(limite),
                    fuente_banco=str(fuente_banco),
                )
            _activate_practica_bloque(bloque)
            st.success(
                f"Práctica lista · {bloque.total_preguntas} ítems · "
                f"prioridad={bloque.resumen_prioridad}"
            )
            st.rerun()
        except (BancoInsuficienteError, ValueError) as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"No se pudo generar la práctica: {exc}")

    bloque = st.session_state.get("practica_activa")
    if not bloque:
        st.info(
            "Pulsa **🧠 Generar Práctica de mis Debilidades** o elige materia/tema "
            "y **Generar Práctica**."
        )
        return

    st.caption(f"Resumen de prioridad: {bloque.resumen_prioridad}")

    def _on_finalize_practica(respuestas: Dict[str, str], tiempos: Dict[str, int]):
        return tutor_engine.finalizar_practica_enfocada(
            practica=bloque,
            respuestas=respuestas,
            tiempos_ms=tiempos,
        )

    _render_mcq_navigator(
        preguntas=list(bloque.preguntas),
        index_key="practica_index",
        answers_key="practica_respuestas",
        tiempos_key="practica_tiempos_ms",
        titulo_progreso="Práctica",
        finalize_label="Finalizar y Evaluar práctica",
        on_finalize=_on_finalize_practica,
        cerrado_key="practica_cerrada",
        resultado_key="practica_resultado",
        modo_origen="practica",
        sesion_id=None,
        tutor_en_vivo=True,
        feedback_inmediato=True,
    )


if __name__ == "__main__":
    init_runtime()
    build_ui()
