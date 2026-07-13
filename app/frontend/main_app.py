import sys
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import streamlit as st
from pypdf import PdfReader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database.db_manager import db_manager
from app.infrastructure.database.user_repository import user_repository
from app.ml.interval_policy import IntervalPolicyService
from app.services.content_service import (
    ExtractedCard,
    cards_to_table_rows,
    extract_study_cards,
    mutate_question_for_review,
)
from app.services.evaluation_service import EvaluationResult, evaluate_answer, interval_policy

STUDENT_HISTORY_COLUMNS = [
    "id_tarjeta",
    "area",
    "tema",
    "pregunta",
    "modo_tarjeta",
    "nivel_dificultad",
    "nota_ia",
    "auditoria_estado",
    "intervalo_recomendado_dias",
    "plan_estudio",
    "creado_en",
]

DUE_COLUMNS = [
    "id_tarjeta",
    "area",
    "tema",
    "pregunta",
    "modo_tarjeta",
    "nivel_dificultad",
    "intervalo_recomendado_dias",
    "fecha_repaso",
]


def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(file_bytes))
    chunks: List[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            chunks.append(text)
    return "\n".join(chunks).strip()


def init_runtime() -> None:
    defaults: Dict[str, Any] = {
        # Estudio activo
        "question_started_at": None,
        "answer_submitted": False,
        "session_locked": False,
        "last_evaluation": None,
        "last_card_id": None,
        "last_reference_text": "",
        "active_card_id": None,
        "active_question_original": "",
        "active_display_question": "",
        "active_reference": "",
        "active_area": "",
        "active_tema": "",
        "active_modo_tarjeta": "concepto",
        "active_nivel_dificultad": 1,
        "active_repetitions": 0,
        "active_easiness": 2.5,
        "active_interval_days": 1,
        "active_is_mutated": False,
        "mutation_detail": "",
        "mutation_cache": {},  # card_id -> pregunta mutada
        "study_queue": [],
        "study_queue_index": 0,
        "study_queue_loaded_at": None,
        "study_queue_owner_id": None,
        # Carga / extracción
        "pdf_text": "",
        "proposed_cards": [],
        "extraction_detail": "",
        "extraction_source": "",
        "plan_estudio": IntervalPolicyService.PLAN_RETENCION,
        "career_hint": "",
        "form_key_counter": 0,
        # Auth multiusuario
        "auth_user": None,
        "usuario_id": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    db_manager.connect()
    db_manager.ensure_schema()


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


def _establish_user_session(auth: Dict[str, Any]) -> None:
    """Guarda el usuario en session_state y limpia cola de estudio ajena."""
    uid = int(auth["id_usuario"])
    st.session_state["auth_user"] = {
        "id_usuario": uid,
        "email": auth["email"],
        "nombre": auth["nombre"],
    }
    st.session_state["usuario_id"] = uid
    st.session_state["study_queue"] = []
    st.session_state["study_queue_index"] = 0
    st.session_state["study_queue_owner_id"] = None
    st.session_state["study_queue_loaded_at"] = None
    st.session_state["active_card_id"] = None
    st.session_state["mutation_cache"] = {}
    st.session_state["proposed_cards"] = []
    _reset_answer_controls()


def _logout() -> None:
    st.session_state["auth_user"] = None
    st.session_state["usuario_id"] = None
    st.session_state["study_queue"] = []
    st.session_state["study_queue_index"] = 0
    st.session_state["study_queue_owner_id"] = None
    st.session_state["study_queue_loaded_at"] = None
    st.session_state["active_card_id"] = None
    st.session_state["proposed_cards"] = []
    st.session_state["mutation_cache"] = {}
    _reset_answer_controls()


def render_auth_gate() -> bool:
    """
    Pantalla de Login / Registro.
    Retorna True si hay sesión activa; False si debe detener el render.
    """
    user = _current_user()
    if user:
        return True

    st.title("🧠 Sistema de Estudio Inteligente")
    st.caption("Inicia sesión o crea una cuenta para guardar tu progreso.")

    tab_login, tab_register = st.tabs(["Iniciar sesión", "Registrarse"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Contraseña", type="password", key="login_password")
            submitted = st.form_submit_button("Entrar", type="primary", width="stretch")
        if submitted:
            try:
                auth = user_repository.authenticate(email=email, password=password)
                if not auth:
                    st.error("Email o contraseña incorrectos.")
                else:
                    _establish_user_session(auth)
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
            submitted = st.form_submit_button("Crear cuenta", type="primary", width="stretch")
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
                    _establish_user_session(created)
                    st.success("Cuenta creada. Ya estás dentro.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"No se pudo registrar: {exc}")

    return False


def _reset_answer_controls() -> None:
    st.session_state["question_started_at"] = None
    st.session_state["answer_submitted"] = False
    st.session_state["session_locked"] = False
    st.session_state["last_evaluation"] = None
    st.session_state["last_card_id"] = None


def _answer_input_key(card_id: Any = None) -> str:
    """Key dinámica: tarjeta + contador → widget nuevo y vacío al incrementar."""
    cid = card_id if card_id is not None else st.session_state.get("active_card_id")
    counter = int(st.session_state.get("form_key_counter") or 0)
    return f"respuesta_input_{cid if cid is not None else 'none'}_{counter}"


def _bump_answer_form_key() -> None:
    """Invalida el text_area actual sin tocar su session_state (evita StreamlitAPIException)."""
    st.session_state["form_key_counter"] = int(st.session_state.get("form_key_counter") or 0) + 1


def _refresh_study_queue(force: bool = False) -> List[Dict[str, Any]]:
    """Carga/recarga la cola de repasos del usuario logueado."""
    usuario_id = _require_usuario_id()
    owner = st.session_state.get("study_queue_owner_id")
    if owner is not None and int(owner) != usuario_id:
        force = True

    queue = list(st.session_state.get("study_queue") or [])
    if force or not queue:
        try:
            queue = db_manager.fetch_due_study_cards(usuario_id=usuario_id, limit=30)
        except Exception:
            queue = []
        st.session_state["study_queue"] = queue
        st.session_state["study_queue_index"] = 0
        st.session_state["study_queue_owner_id"] = usuario_id
        st.session_state["study_queue_loaded_at"] = time.time()
    return queue


def _prepare_card_for_study(card: Dict[str, Any]) -> None:
    """Fija tarjeta activa y muta la pregunta si ya fue repasada antes."""
    card_id = int(card.get("id_tarjeta"))
    original = str(card.get("pregunta") or "").strip()
    reference = str(card.get("respuesta_referencia") or "").strip()
    reps = int(card.get("repetitions_count") or 0)
    modo = str(card.get("modo_tarjeta") or "concepto").lower()
    nivel = int(card.get("nivel_dificultad") or 1)

    st.session_state["active_card_id"] = card_id
    st.session_state["active_question_original"] = original
    st.session_state["active_reference"] = reference
    st.session_state["active_area"] = str(card.get("area") or "General")
    st.session_state["active_tema"] = str(card.get("tema") or "General")
    st.session_state["active_modo_tarjeta"] = modo
    st.session_state["active_nivel_dificultad"] = max(1, min(3, nivel))
    st.session_state["active_repetitions"] = reps
    st.session_state["active_easiness"] = float(card.get("easiness_factor") or 2.5)
    st.session_state["active_interval_days"] = int(card.get("intervalo_recomendado_dias") or 1)

    cache: Dict[Any, str] = dict(st.session_state.get("mutation_cache") or {})
    if reps > 0:
        if card_id in cache and cache[card_id].strip():
            display = cache[card_id]
            detail = "Pregunta mutada (caché de sesión)."
            method_ok = True
        else:
            mutation = mutate_question_for_review(
                pregunta_original=original,
                respuesta_referencia=reference,
                modo_tarjeta=modo,
                nivel_dificultad=nivel,
            )
            display = mutation.pregunta_mutada
            detail = mutation.detail
            cache[card_id] = display
            st.session_state["mutation_cache"] = cache
            method_ok = mutation.method.startswith("openrouter")
        st.session_state["active_display_question"] = display
        st.session_state["active_is_mutated"] = True
        st.session_state["mutation_detail"] = detail
        if not method_ok and "local" in detail.lower():
            st.session_state["mutation_detail"] = detail
    else:
        st.session_state["active_display_question"] = original
        st.session_state["active_is_mutated"] = False
        st.session_state["mutation_detail"] = "Primera vez: pregunta original (sin mutación)."


def _advance_study_queue() -> None:
    _bump_answer_form_key()
    queue = list(st.session_state.get("study_queue") or [])
    idx = int(st.session_state.get("study_queue_index") or 0)
    if idx + 1 < len(queue):
        st.session_state["study_queue_index"] = idx + 1
        _prepare_card_for_study(queue[idx + 1])
    else:
        # Recargar cola tras terminar el lote.
        st.session_state["study_queue"] = []
        st.session_state["study_queue_index"] = 0
        st.session_state["active_card_id"] = None
        st.session_state["active_display_question"] = ""
        st.session_state["active_question_original"] = ""
    _reset_answer_controls()


def _approve_proposed_cards(
    cards: List[ExtractedCard],
    plan_estudio: str,
    origen_contenido: str,
) -> List[int]:
    usuario_id = _require_usuario_id()
    ids: List[int] = []
    for card in cards:
        card_id = db_manager.insert_memory_card(
            usuario_id=usuario_id,
            area=card.area,
            tema=card.tema,
            pregunta=card.pregunta,
            respuesta_referencia=card.respuesta_referencia,
            respuesta_estudiante=None,
            nota_ia=None,
            auditoria_estado="Pendiente",
            auditoria_tiempo_ms=None,
            intervalo_recomendado_dias=1,
            plan_estudio=plan_estudio,
            tipo_pregunta="open",
            modo_simulacro=False,
            origen_contenido=origen_contenido,
            repetitions_count=0,
            easiness_factor=2.5,
            modo_tarjeta=card.modo_tarjeta,
            nivel_dificultad=card.nivel_dificultad,
        )
        ids.append(card_id)
    # Invalida cola para que Estudiar vea las nuevas.
    st.session_state["study_queue"] = []
    return ids


# ---------------------------------------------------------------------------
# Pestaña 1 — Estudiar
# ---------------------------------------------------------------------------
def render_study_tab() -> None:
    st.subheader("📚 Estudiar (Repasos adaptativos)")
    st.caption(
        "Si la tarjeta ya fue repasada, verás una pregunta mutada. "
        "La calificación usa siempre la respuesta de referencia original."
    )

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Actualizar cola de hoy", width="stretch"):
            _refresh_study_queue(force=True)
            st.session_state["active_card_id"] = None
            _reset_answer_controls()
            st.rerun()
    with col_b:
        plan_estudio = st.selectbox(
            "Plan al guardar el repaso",
            options=[IntervalPolicyService.PLAN_INTENSIVO, IntervalPolicyService.PLAN_RETENCION],
            index=0
            if st.session_state.get("plan_estudio") == IntervalPolicyService.PLAN_INTENSIVO
            else 1,
            key="plan_estudio_study",
        )
        st.session_state["plan_estudio"] = plan_estudio

    queue = _refresh_study_queue(force=False)
    if not queue:
        st.success("No tienes repasos pendientes ni vencidos. Carga material en la pestaña ➕.")
        return

    idx = int(st.session_state.get("study_queue_index") or 0)
    idx = max(0, min(idx, len(queue) - 1))
    st.session_state["study_queue_index"] = idx
    current = queue[idx]

    # Preparar tarjeta solo si cambió el id activo (evita remutar al cambiar de tab).
    if st.session_state.get("active_card_id") != int(current.get("id_tarjeta")):
        with st.spinner("Preparando tarjeta adaptativa..."):
            _prepare_card_for_study(current)
        _bump_answer_form_key()

    st.progress((idx + 1) / len(queue), text=f"Tarjeta {idx + 1} de {len(queue)}")

    area = st.session_state.get("active_area", "")
    tema = st.session_state.get("active_tema", "")
    modo = st.session_state.get("active_modo_tarjeta", "concepto")
    nivel = int(st.session_state.get("active_nivel_dificultad") or 1)
    display_q = str(st.session_state.get("active_display_question") or "")
    reference = str(st.session_state.get("active_reference") or "")
    is_mutated = bool(st.session_state.get("active_is_mutated"))

    st.markdown(f"**Área:** {area} · **Tema:** {tema}")
    st.markdown(
        f"**Modo:** `{modo}` · **Nivel:** `{nivel}` · "
        f"**Repeticiones previas:** `{st.session_state.get('active_repetitions', 0)}`"
    )

    if is_mutated:
        st.info("🧬 Pregunta mutada (anti-loro). La referencia de evaluación no cambia.")
        if st.session_state.get("mutation_detail"):
            st.caption(st.session_state["mutation_detail"])
    else:
        st.caption(st.session_state.get("mutation_detail", "Pregunta original"))

    st.markdown(f"### {display_q}")

    locked = bool(st.session_state.get("session_locked"))
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("Comenzar respuesta", disabled=locked or not display_q, width="stretch"):
            st.session_state["question_started_at"] = time.time()
            st.session_state["session_locked"] = True
            st.session_state["answer_submitted"] = False
            st.session_state["last_evaluation"] = None
            st.rerun()
    with c2:
        if st.button("Cancelar", disabled=not locked, width="stretch"):
            _reset_answer_controls()
            st.rerun()
    with c3:
        if st.button("Saltar tarjeta", disabled=locked, width="stretch"):
            _advance_study_queue()
            st.rerun()

    can_answer = locked and not st.session_state.get("answer_submitted")
    if st.session_state.get("question_started_at") and can_answer:
        elapsed_live = int((time.time() - st.session_state["question_started_at"]) * 1000)
        st.caption(f"Cronómetro: {elapsed_live} ms")

    help_txt = (
        "Resultado exacto (evaluación estricta)."
        if modo == "ejercicio"
        else "Explica con tus palabras (evaluación semántica)."
    )
    answer_key = _answer_input_key(st.session_state.get("active_card_id"))
    respuesta = st.text_area(
        "Tu respuesta",
        disabled=not can_answer,
        key=answer_key,
        help=help_txt,
        height=140,
    )

    def _on_enviar_respuesta() -> None:
        # Guarda el texto actual y rota la key antes del rerun (widget limpio).
        st.session_state["_pending_study_answer"] = st.session_state.get(answer_key, "")
        _bump_answer_form_key()

    if st.button(
        "Enviar respuesta",
        type="primary",
        disabled=not can_answer,
        on_click=_on_enviar_respuesta,
    ):
        pending = str(st.session_state.pop("_pending_study_answer", None) or respuesta or "").strip()
        if not pending:
            st.warning("Escribe una respuesta antes de enviar.")
            return
        if not reference:
            st.error("Esta tarjeta no tiene respuesta de referencia.")
            return

        elapsed = int((time.time() - st.session_state["question_started_at"]) * 1000)
        with st.spinner("Evaluando comprensión..."):
            try:
                result = evaluate_answer(
                    question_text=display_q,
                    reference_text=reference,
                    student_answer=pending,
                    response_time_ms=elapsed,
                    previous_repetitions=int(st.session_state.get("active_repetitions") or 0),
                    previous_easiness=float(st.session_state.get("active_easiness") or 2.5),
                    previous_interval_days=int(st.session_state.get("active_interval_days") or 1),
                    modo_tarjeta=modo,
                )
            except Exception:
                result = EvaluationResult(
                    nota_ia=0.0,
                    auditoria_estado="Error",
                    auditoria_tiempo_ms=elapsed,
                    intervalo_dias=1,
                    nlp_method="fallback-error",
                    anomaly_method="fallback-error",
                    interval_strategy="fallback-error",
                    quality=0,
                    repetitions=0,
                    easiness=2.5,
                    key_points=[],
                    conceptual_errors=["No se pudo procesar el análisis."],
                    writing_suggestion="Intenta de nuevo.",
                    feedback_method="fallback-error",
                    modo_tarjeta=modo,
                )

        result.intervalo_dias = interval_policy.apply_study_plan(
            interval_days=result.intervalo_dias,
            study_plan=st.session_state.get("plan_estudio", IntervalPolicyService.PLAN_RETENCION),
        )

        saved_id = None
        card_id = st.session_state.get("active_card_id")
        try:
            if not card_id:
                raise RuntimeError("No hay tarjeta activa para guardar el repaso.")
            saved_id = db_manager.record_study_response(
                id_tarjeta=int(card_id),
                usuario_id=_require_usuario_id(),
                respuesta_estudiante=pending,
                nota_ia=result.nota_ia,
                auditoria_estado=result.auditoria_estado,
                auditoria_tiempo_ms=result.auditoria_tiempo_ms,
                intervalo_recomendado_dias=result.intervalo_dias,
                plan_estudio=st.session_state.get("plan_estudio"),
                repetitions_count=result.repetitions,
                easiness_factor=result.easiness,
            )
        except Exception as exc:
            st.error(f"No se pudo guardar el repaso en PostgreSQL: {exc}")

        st.session_state["answer_submitted"] = True
        st.session_state["session_locked"] = False
        st.session_state["question_started_at"] = None
        st.session_state["last_evaluation"] = result
        st.session_state["last_card_id"] = saved_id
        st.session_state["last_reference_text"] = reference
        # Limpia mutación de esta tarjeta para el próximo ciclo.
        cache = dict(st.session_state.get("mutation_cache") or {})
        cache.pop(st.session_state.get("active_card_id"), None)
        st.session_state["mutation_cache"] = cache
        st.rerun()

    result = st.session_state.get("last_evaluation")
    if st.session_state.get("answer_submitted") and result is not None:
        st.success("Repaso guardado.")
        m1, m2, m3 = st.columns(3)
        m1.metric("Nota", f"{result.nota_ia}/5")
        m2.metric("Auditoría", result.auditoria_estado)
        m3.metric("Próxima revisión", f"+{result.intervalo_dias} d")
        st.caption(
            f"NLP={result.nlp_method} | Mutada={is_mutated} | "
            f"ID={st.session_state.get('last_card_id')}"
        )
        st.write("**Tutoría**")
        for item in result.key_points:
            st.write(f"- {item}")
        if result.conceptual_errors:
            st.write("**Errores**")
            for item in result.conceptual_errors:
                st.write(f"- {item}")
        st.info(result.writing_suggestion)
        with st.expander("Respuesta de referencia (original)"):
            st.write(st.session_state.get("last_reference_text", ""))
        if st.button("Siguiente tarjeta", type="primary"):
            _advance_study_queue()
            st.rerun()


# ---------------------------------------------------------------------------
# Pestaña 2 — Cargar material
# ---------------------------------------------------------------------------
def render_load_tab() -> None:
    st.subheader("➕ Cargar material")
    st.caption("Sube PDF o apuntes → extracción inteligente → aprueba tarjetas.")

    if st.session_state.get("session_locked"):
        st.warning("Hay una respuesta en curso en Estudiar. Cancélala antes de cargar material.")
        return

    career_hint = st.text_input(
        "Carrera objetivo (opcional)",
        value=st.session_state.get("career_hint", ""),
        key="career_hint_input",
    )
    st.session_state["career_hint"] = career_hint

    plan_estudio = st.selectbox(
        "Plan de estudio para tarjetas nuevas",
        options=[IntervalPolicyService.PLAN_INTENSIVO, IntervalPolicyService.PLAN_RETENCION],
        index=0
        if st.session_state.get("plan_estudio") == IntervalPolicyService.PLAN_INTENSIVO
        else 1,
        key="plan_estudio_load",
    )
    st.session_state["plan_estudio"] = plan_estudio

    uploaded_pdf = st.file_uploader("PDF de apuntes", type=["pdf"], key="pdf_uploader")
    manual_source = st.text_area("O pega apuntes", key="manual_source_input", height=160)

    if uploaded_pdf is not None and not st.session_state.get("pdf_text"):
        try:
            pdf_text = extract_text_from_pdf(uploaded_pdf.read())
            if pdf_text:
                st.session_state["pdf_text"] = pdf_text
                st.success("PDF procesado.")
            else:
                st.warning("PDF sin texto legible.")
        except Exception:
            st.error("No se pudo leer el PDF.")

    if st.session_state.get("pdf_text"):
        st.caption(f"PDF en memoria: {len(st.session_state['pdf_text'])} caracteres")
        if st.button("Quitar PDF"):
            st.session_state["pdf_text"] = ""
            st.rerun()

    chunks: List[str] = []
    origins: List[str] = []
    if st.session_state.get("pdf_text"):
        chunks.append(st.session_state["pdf_text"])
        origins.append("pdf")
    if manual_source.strip():
        chunks.append(manual_source.strip())
        origins.append("manual")
    merged = "\n\n".join(chunks)
    origen = "+".join(origins) if origins else "manual"

    c1, c2 = st.columns(2)
    with c1:
        extract_clicked = st.button(
            "Extraer tarjetas con IA",
            type="primary",
            disabled=not merged.strip(),
            width="stretch",
        )
    with c2:
        if st.button("Limpiar propuesta", width="stretch"):
            st.session_state["proposed_cards"] = []
            st.session_state["extraction_detail"] = ""
            st.session_state["extraction_source"] = ""
            st.rerun()

    if extract_clicked:
        with st.spinner("Diseñador instruccional extrayendo ~10 tarjetas..."):
            try:
                result = extract_study_cards(merged, max_cards=10, career_hint=career_hint or None)
                st.session_state["proposed_cards"] = result.cards
                st.session_state["extraction_source"] = result.source
                st.session_state["extraction_detail"] = result.detail
            except Exception as exc:
                st.error(f"Extracción fallida: {exc}")

    if st.session_state.get("extraction_detail"):
        src = st.session_state.get("extraction_source")
        if src == "openrouter":
            st.success(st.session_state["extraction_detail"])
        else:
            st.warning(st.session_state["extraction_detail"])

    proposed: List[ExtractedCard] = list(st.session_state.get("proposed_cards") or [])
    if proposed:
        st.write("#### Revisión de propuesta")
        st.dataframe(cards_to_table_rows(proposed), width="stretch")
        if st.button("Aprobar y Generar Tarjetas", type="primary", width="stretch"):
            try:
                ids = _approve_proposed_cards(proposed, plan_estudio=plan_estudio, origen_contenido=origen)
                st.session_state["proposed_cards"] = []
                st.success(f"{len(ids)} tarjetas guardadas. Ve a 📚 Estudiar.")
                st.rerun()
            except Exception as exc:
                st.error(f"No se pudieron guardar: {exc}")


# ---------------------------------------------------------------------------
# Pestaña 3 — Dashboard
# ---------------------------------------------------------------------------
def render_dashboard_tab() -> None:
    st.subheader("📊 Dashboard")
    try:
        dashboard = db_manager.fetch_progress_dashboard(
            usuario_id=_require_usuario_id(),
            due_limit=20,
        )
        a, b = st.columns(2)
        a.metric("Temas dominados", dashboard.get("temas_dominados", 0))
        b.metric("Repasos para hoy", dashboard.get("total_repasos_hoy", 0))

        due_rows = dashboard.get("repasos_hoy", [])
        if due_rows:
            st.write("**Vencidas hoy**")
            safe = [{k: r.get(k) for k in DUE_COLUMNS if k in r} for r in due_rows]
            st.dataframe(safe, width="stretch")
        else:
            st.success("Sin repasos vencidos.")

        by_plan = dashboard.get("por_plan", [])
        if by_plan:
            st.write("**Por plan**")
            st.dataframe(by_plan, width="stretch")
    except Exception:
        st.warning("No se pudo cargar el dashboard.")

    st.write("---")
    st.write("**Historial reciente**")
    try:
        cards = db_manager.fetch_memory_cards(usuario_id=_require_usuario_id(), limit=25)
        if cards:
            safe = [{k: c.get(k) for k in STUDENT_HISTORY_COLUMNS if k in c} for c in cards]
            st.dataframe(safe, width="stretch")
        else:
            st.info("Aún no hay tarjetas.")
    except Exception:
        st.warning("No se pudo leer el historial.")


def build_ui() -> None:
    st.set_page_config(page_title="Sistema de Estudio Inteligente", page_icon="🧠", layout="wide")

    if not render_auth_gate():
        return

    user = _current_user() or {}
    top_l, top_r = st.columns([3, 1])
    with top_l:
        st.title("🧠 Sistema de Estudio Inteligente")
        st.caption(
            f"Sesión: **{user.get('nombre', '')}** ({user.get('email', '')}) · "
            "solo ves y estudias tus propias tarjetas."
        )
    with top_r:
        st.write("")
        if st.button("Cerrar sesión", width="stretch"):
            _logout()
            st.rerun()

    tab_study, tab_load, tab_dash = st.tabs(
        ["📚 Estudiar", "➕ Cargar material", "📊 Dashboard"]
    )
    with tab_study:
        render_study_tab()
    with tab_load:
        render_load_tab()
    with tab_dash:
        render_dashboard_tab()


if __name__ == "__main__":
    init_runtime()
    build_ui()
