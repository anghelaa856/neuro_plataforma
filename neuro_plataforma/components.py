"""UI Reflex — shell autenticado, tabs y módulos del producto."""

from __future__ import annotations

import reflex as rx

from neuro_plataforma.state import (
    AppState,
    AuthState,
    DashboardState,
    IngestState,
    StudyState,
)
from neuro_plataforma.styles import COLORS, OPTION_BASE, PAGE_BG, SURFACE_CARD


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def _card(*children, **props) -> rx.Component:
    base = {
        "background": SURFACE_CARD["background"],
        "border": SURFACE_CARD["border"],
        "border_radius": SURFACE_CARD["border_radius"],
        "box_shadow": SURFACE_CARD["box_shadow"],
        "width": "100%",
    }
    base.update(props)
    return rx.box(*children, **base)


def brand_mark(compact: bool = False) -> rx.Component:
    return rx.hstack(
        rx.center(
            rx.icon("stethoscope", size=18, color="white"),
            width="36px",
            height="36px",
            border_radius="10px",
            background=COLORS["brand"],
            flex_shrink="0",
        ),
        rx.vstack(
            rx.text(
                "Neuro Plataforma",
                weight="bold",
                size="2",
                color=COLORS["ink"],
            ),
            rx.cond(
                compact,
                rx.fragment(),
                rx.text("UNA · Admisión Medicina", size="1", color=COLORS["muted"]),
            ),
            spacing="0",
            align="start",
        ),
        spacing="3",
        align="center",
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def auth_view() -> rx.Component:
    return rx.center(
        rx.box(
            rx.vstack(
                brand_mark(),
                rx.heading(
                    "Tu camino a Medicina",
                    size="7",
                    color=COLORS["ink"],
                    letter_spacing="-0.02em",
                ),
                rx.text(
                    "Práctica enfocada, simulacro oficial UNA y tu Índice Medicina.",
                    size="3",
                    color=COLORS["muted"],
                    line_height="1.5",
                ),
                rx.cond(
                    AuthState.boot_message != "",
                    rx.callout(
                        AuthState.boot_message,
                        icon="info",
                        color_scheme="amber",
                        size="1",
                        width="100%",
                    ),
                ),
                rx.hstack(
                    rx.button(
                        "Iniciar sesión",
                        variant=rx.cond(AuthState.auth_mode == "login", "solid", "soft"),
                        color_scheme="blue",
                        on_click=AuthState.set_mode_login,
                        flex="1",
                    ),
                    rx.button(
                        "Registrarse",
                        variant=rx.cond(
                            AuthState.auth_mode == "register", "solid", "soft"
                        ),
                        color_scheme="blue",
                        on_click=AuthState.set_mode_register,
                        flex="1",
                    ),
                    width="100%",
                    spacing="2",
                ),
                _card(
                    rx.vstack(
                        rx.cond(
                            AuthState.auth_mode == "register",
                            rx.vstack(
                                rx.text("Nombre", size="2", weight="medium"),
                                rx.input(
                                    placeholder="Tu nombre",
                                    value=AuthState.form_nombre,
                                    on_change=AuthState.set_form_nombre,
                                    width="100%",
                                    size="3",
                                ),
                                spacing="1",
                                width="100%",
                            ),
                        ),
                        rx.vstack(
                            rx.text("Email", size="2", weight="medium"),
                            rx.input(
                                placeholder="tu@email.com",
                                type="email",
                                value=AuthState.form_email,
                                on_change=AuthState.set_form_email,
                                width="100%",
                                size="3",
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        rx.vstack(
                            rx.text("Contraseña", size="2", weight="medium"),
                            rx.input(
                                placeholder="Mínimo 6 caracteres",
                                type="password",
                                value=AuthState.form_password,
                                on_change=AuthState.set_form_password,
                                width="100%",
                                size="3",
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        rx.cond(
                            AuthState.auth_error != "",
                            rx.callout(
                                AuthState.auth_error,
                                icon="triangle-alert",
                                color_scheme="red",
                                size="1",
                                width="100%",
                            ),
                        ),
                        rx.cond(
                            AuthState.auth_mode == "login",
                            rx.button(
                                "Entrar",
                                on_click=AuthState.login,
                                loading=AuthState.loading,
                                width="100%",
                                size="3",
                                color_scheme="blue",
                                high_contrast=True,
                            ),
                            rx.button(
                                "Crear cuenta",
                                on_click=AuthState.register,
                                loading=AuthState.loading,
                                width="100%",
                                size="3",
                                color_scheme="blue",
                                high_contrast=True,
                            ),
                        ),
                        spacing="4",
                        width="100%",
                    ),
                    padding="1.25rem",
                ),
                spacing="5",
                width="100%",
                align="start",
            ),
            width="100%",
            max_width="420px",
            padding="1.25rem",
        ),
        min_height="100vh",
        width="100%",
        background=PAGE_BG,
        align_items="flex-start",
        padding_y="2rem",
    )


# ---------------------------------------------------------------------------
# Shell + nav
# ---------------------------------------------------------------------------


def _nav_btn(label: str, tab: str, icon: str) -> rx.Component:
    active = AppState.tab == tab
    return rx.button(
        rx.hstack(
            rx.icon(icon, size=16),
            rx.text(label, size="2"),
            spacing="2",
            align="center",
        ),
        on_click=AppState.set_tab(tab),
        variant=rx.cond(active, "solid", "ghost"),
        color_scheme=rx.cond(active, "blue", "gray"),
        width="100%",
        justify_content="flex-start",
        size="2",
        radius="large",
    )


def app_shell(content: rx.Component) -> rx.Component:
    return rx.box(
        rx.box(
            # Desktop sidebar
            rx.box(
                rx.vstack(
                    brand_mark(compact=True),
                    rx.divider(),
                    _nav_btn("Mis Guías", "guias", "folder-up"),
                    _nav_btn("Práctica", "practica", "dumbbell"),
                    _nav_btn("Simulacro", "simulacro", "timer"),
                    _nav_btn("Banco Admin", "admin", "shield"),
                    _nav_btn("Rendimiento", "dashboard", "gauge"),
                    rx.spacer(),
                    rx.vstack(
                        rx.text(AuthState.nombre, size="2", weight="bold"),
                        rx.text(AuthState.email, size="1", color=COLORS["muted"]),
                        rx.button(
                            "Cerrar sesión",
                            on_click=AuthState.logout,
                            variant="soft",
                            color_scheme="gray",
                            size="1",
                            width="100%",
                        ),
                        spacing="1",
                        width="100%",
                    ),
                    spacing="2",
                    height="100%",
                    width="100%",
                    align="start",
                ),
                display=["none", "none", "flex"],
                width="240px",
                min_width="240px",
                padding="1rem",
                border_right=f"1px solid {COLORS['line']}",
                background=COLORS["surface"],
                min_height="100vh",
                position="sticky",
                top="0",
            ),
            # Main column
            rx.box(
                # Mobile top bar
                rx.box(
                    rx.vstack(
                        rx.hstack(
                            brand_mark(compact=True),
                            rx.spacer(),
                            rx.button(
                                rx.icon("log-out", size=16),
                                on_click=AuthState.logout,
                                variant="soft",
                                size="1",
                            ),
                            width="100%",
                            align="center",
                        ),
                        rx.scroll_area(
                            rx.hstack(
                                rx.button(
                                    "Guías",
                                    size="1",
                                    variant=rx.cond(AppState.tab == "guias", "solid", "soft"),
                                    on_click=AppState.set_tab("guias"),
                                ),
                                rx.button(
                                    "Práctica",
                                    size="1",
                                    variant=rx.cond(
                                        AppState.tab == "practica", "solid", "soft"
                                    ),
                                    on_click=AppState.set_tab("practica"),
                                ),
                                rx.button(
                                    "Simulacro",
                                    size="1",
                                    variant=rx.cond(
                                        AppState.tab == "simulacro", "solid", "soft"
                                    ),
                                    on_click=AppState.set_tab("simulacro"),
                                ),
                                rx.button(
                                    "Admin",
                                    size="1",
                                    variant=rx.cond(AppState.tab == "admin", "solid", "soft"),
                                    on_click=AppState.set_tab("admin"),
                                ),
                                rx.button(
                                    "Rendimiento",
                                    size="1",
                                    variant=rx.cond(
                                        AppState.tab == "dashboard", "solid", "soft"
                                    ),
                                    on_click=AppState.set_tab("dashboard"),
                                ),
                                spacing="2",
                                padding_bottom="0.25rem",
                            ),
                            scrollbars="horizontal",
                            type="auto",
                            width="100%",
                        ),
                        spacing="3",
                        width="100%",
                    ),
                    display=["flex", "flex", "none"],
                    padding="0.85rem 1rem",
                    background=COLORS["surface"],
                    border_bottom=f"1px solid {COLORS['line']}",
                    width="100%",
                ),
                rx.box(
                    content,
                    width="100%",
                    max_width="720px",
                    padding="1rem",
                    margin_x="auto",
                ),
                flex="1",
                min_width="0",
                background=PAGE_BG,
                min_height="100vh",
            ),
            display="flex",
            width="100%",
            align_items="stretch",
        ),
        width="100%",
        min_height="100vh",
    )


# ---------------------------------------------------------------------------
# Option / quiz widgets
# ---------------------------------------------------------------------------


def _option_bg(letter: str):
    return rx.cond(
        StudyState.answered,
        rx.cond(
            StudyState.revealed_correct == letter,
            COLORS["success_bg"],
            rx.cond(
                StudyState.selected == letter,
                COLORS["danger_bg"],
                "#F8FAFC",
            ),
        ),
        rx.cond(
            StudyState.selected == letter,
            COLORS["brand_soft"],
            COLORS["surface"],
        ),
    )


def _option_border(letter: str):
    return rx.cond(
        StudyState.answered,
        rx.cond(
            StudyState.revealed_correct == letter,
            f"1.5px solid {COLORS['success_border']}",
            rx.cond(
                StudyState.selected == letter,
                f"1.5px solid {COLORS['danger_border']}",
                f"1.5px solid {COLORS['line']}",
            ),
        ),
        rx.cond(
            StudyState.selected == letter,
            f"1.5px solid {COLORS['brand']}",
            f"1.5px solid {COLORS['line']}",
        ),
    )


def _option_color(letter: str):
    return rx.cond(
        StudyState.answered,
        rx.cond(
            StudyState.revealed_correct == letter,
            COLORS["success"],
            rx.cond(
                StudyState.selected == letter,
                COLORS["danger"],
                COLORS["muted"],
            ),
        ),
        rx.cond(
            StudyState.selected == letter,
            COLORS["brand"],
            COLORS["ink"],
        ),
    )


def option_button(letter: str, text: rx.Var[str]) -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.box(
                rx.text(letter, size="1", weight="bold"),
                width="22px",
                height="22px",
                border_radius="999px",
                background=COLORS["bg"],
                display="flex",
                align_items="center",
                justify_content="center",
                flex_shrink="0",
                color=_option_color(letter),
            ),
            rx.text(
                text,
                size="3",
                weight="medium",
                line_height="1.4",
                color=_option_color(letter),
            ),
            spacing="3",
            align="center",
            width="100%",
        ),
        on_click=StudyState.select_option(letter),
        width="100%",
        background=_option_bg(letter),
        border=_option_border(letter),
        pointer_events=rx.cond(
            StudyState.feedback_inmediato,
            rx.cond(StudyState.answered, "none", "auto"),
            "auto",
        ),
        style=OPTION_BASE,
        role="button",
    )


def quiz_panel() -> rx.Component:
    return _card(
        rx.vstack(
            rx.hstack(
                rx.badge(StudyState.category, color_scheme="blue", variant="soft"),
                rx.spacer(),
                rx.text(
                    StudyState.question_number,
                    " / ",
                    StudyState.total_questions,
                    size="2",
                    color=COLORS["muted"],
                ),
                width="100%",
            ),
            rx.progress(
                value=StudyState.progress_percent,
                width="100%",
                color_scheme="blue",
                radius="full",
            ),
            rx.heading(StudyState.stem, size="5", color=COLORS["ink"], line_height="1.35"),
            rx.vstack(
                option_button("A", StudyState.option_a),
                option_button("B", StudyState.option_b),
                option_button("C", StudyState.option_c),
                option_button("D", StudyState.option_d),
                option_button("E", StudyState.option_e),
                spacing="2",
                width="100%",
            ),
            rx.cond(
                StudyState.feedback_inmediato,
                rx.cond(
                    ~StudyState.answered,
                    rx.button(
                        "Comprobar respuesta",
                        on_click=StudyState.check_answer,
                        disabled=~StudyState.has_selection,
                        width="100%",
                        size="3",
                        color_scheme="blue",
                        high_contrast=True,
                    ),
                    rx.box(
                        rx.vstack(
                            rx.text(
                                rx.cond(StudyState.is_correct, "¡Correcto!", "Incorrecto"),
                                weight="bold",
                                color=rx.cond(
                                    StudyState.is_correct,
                                    COLORS["success"],
                                    COLORS["danger"],
                                ),
                            ),
                            rx.text(
                                StudyState.revealed_explanation,
                                size="2",
                                color=COLORS["slate"],
                            ),
                            # Coach IA: solo tras error, a petición (sin auto-gasto API)
                            rx.cond(
                                ~StudyState.is_correct,
                                rx.vstack(
                                    rx.cond(
                                        StudyState.can_request_coach,
                                        rx.button(
                                            "Profundizar mi error con IA",
                                            on_click=StudyState.request_error_coach,
                                            variant="soft",
                                            color_scheme="blue",
                                            size="2",
                                            width="100%",
                                        ),
                                    ),
                                    rx.cond(
                                        StudyState.coach_loading,
                                        rx.hstack(
                                            rx.spinner(size="1"),
                                            rx.text(
                                                "El tutor está razonando tu error…",
                                                size="1",
                                                color=COLORS["muted"],
                                            ),
                                            spacing="2",
                                            align="center",
                                            width="100%",
                                            padding_y="0.35rem",
                                        ),
                                    ),
                                    rx.cond(
                                        StudyState.coach_error != "",
                                        rx.text(
                                            StudyState.coach_error,
                                            size="1",
                                            color=COLORS["danger"],
                                        ),
                                    ),
                                    rx.cond(
                                        StudyState.coach_visible,
                                        rx.box(
                                            rx.vstack(
                                                rx.hstack(
                                                    rx.badge(
                                                        "Tutor IA",
                                                        color_scheme="blue",
                                                        variant="soft",
                                                    ),
                                                    rx.badge(
                                                        StudyState.coach_nivel_label,
                                                        color_scheme=rx.cond(
                                                            StudyState.coach_nivel == 1,
                                                            "green",
                                                            rx.cond(
                                                                StudyState.coach_nivel == 3,
                                                                "red",
                                                                "blue",
                                                            ),
                                                        ),
                                                        variant="outline",
                                                    ),
                                                    rx.cond(
                                                        StudyState.coach_kpi_fuente != "sesion",
                                                        rx.badge(
                                                            StudyState.coach_kpi_fuente,
                                                            color_scheme="gray",
                                                            variant="soft",
                                                        ),
                                                    ),
                                                    rx.text(
                                                        "Análisis de tu razonamiento",
                                                        size="1",
                                                        weight="medium",
                                                        color=COLORS["brand"],
                                                    ),
                                                    spacing="2",
                                                    align="center",
                                                    flex_wrap="wrap",
                                                ),
                                                rx.text(
                                                    StudyState.coach_text,
                                                    size="2",
                                                    color=COLORS["ink"],
                                                    white_space="pre-wrap",
                                                    line_height="1.55",
                                                ),
                                                spacing="2",
                                                width="100%",
                                            ),
                                            padding="0.85rem",
                                            border_radius="12px",
                                            background=COLORS["brand_soft"],
                                            border=f"1px solid {COLORS['brand_mid']}",
                                            width="100%",
                                        ),
                                    ),
                                    spacing="2",
                                    width="100%",
                                    padding_top="0.35rem",
                                ),
                            ),
                            spacing="2",
                            width="100%",
                        ),
                        padding="1rem",
                        border_radius="14px",
                        background=rx.cond(
                            StudyState.is_correct,
                            COLORS["success_bg"],
                            COLORS["danger_bg"],
                        ),
                        width="100%",
                    ),
                ),
            ),
            rx.hstack(
                rx.button(
                    "Anterior",
                    on_click=StudyState.go_prev,
                    disabled=StudyState.is_first,
                    variant="soft",
                    flex="1",
                ),
                rx.button(
                    StudyState.next_label,
                    on_click=StudyState.go_next,
                    disabled=rx.cond(
                        StudyState.feedback_inmediato,
                        ~StudyState.can_advance,
                        False,
                    ),
                    color_scheme="blue",
                    high_contrast=True,
                    flex="1",
                ),
                width="100%",
                spacing="2",
            ),
            spacing="4",
            width="100%",
        ),
        padding="1.25rem",
    )


def results_panel() -> rx.Component:
    return _card(
        rx.vstack(
            rx.heading(StudyState.result_titulo, size="6", color=COLORS["ink"]),
            rx.text(StudyState.result_subtitle, color=COLORS["muted"]),
            rx.heading(StudyState.score_label, size="8", color=COLORS["brand"]),
            rx.hstack(
                rx.badge(StudyState.result_bruto_label, color_scheme="blue", variant="soft"),
                rx.badge(
                    StudyState.result_ponderado_label,
                    color_scheme="indigo",
                    variant="soft",
                ),
                spacing="2",
                wrap="wrap",
            ),
            rx.cond(
                StudyState.persist_status != "",
                rx.callout(
                    StudyState.persist_status,
                    icon="database",
                    color_scheme="green",
                    width="100%",
                ),
            ),
            rx.button(
                "Volver al menú de estudio",
                on_click=StudyState.back_to_setup,
                width="100%",
                size="3",
                color_scheme="blue",
                high_contrast=True,
            ),
            spacing="4",
            width="100%",
            align="center",
        ),
        padding="1.5rem",
    )


# ---------------------------------------------------------------------------
# Tabs content
# ---------------------------------------------------------------------------


def practica_tab() -> rx.Component:
    return rx.cond(
        StudyState.phase == "quiz",
        quiz_panel(),
        rx.cond(
            StudyState.phase == "results",
            results_panel(),
            _card(
                rx.vstack(
                    rx.heading("Práctica Enfocada", size="6"),
                    rx.text(
                        "Feedback inmediato · SRS · opciones A–E mezcladas cada sesión · "
                        "Modo Global disponible.",
                        color=COLORS["muted"],
                        size="2",
                    ),
                    rx.cond(
                        StudyState.error_msg != "",
                        rx.callout(
                            StudyState.error_msg,
                            icon="triangle-alert",
                            color_scheme="red",
                            width="100%",
                        ),
                    ),
                    rx.cond(
                        StudyState.status_msg != "",
                        rx.callout(
                            StudyState.status_msg,
                            icon="info",
                            color_scheme="blue",
                            width="100%",
                        ),
                    ),
                    rx.text("Fuente del banco", size="2", weight="medium"),
                    rx.select(
                        ["todo", "oficial", "mis_guias"],
                        value=StudyState.fuente_banco,
                        on_change=StudyState.set_fuente,
                        width="100%",
                    ),
                    rx.text("Materia / alcance", size="2", weight="medium"),
                    rx.select(
                        StudyState.materia_options,
                        placeholder="Todas las materias o una específica",
                        on_change=StudyState.set_materia,
                        width="100%",
                    ),
                    rx.text(
                        "Tip: elige «Todas las materias (Modo Global)» para un mix "
                        "tipo examen, o una materia para foco.",
                        size="1",
                        color=COLORS["muted"],
                    ),
                    rx.hstack(
                        rx.button(
                            "Cargar catálogo",
                            on_click=StudyState.load_catalog,
                            variant="soft",
                            flex="1",
                        ),
                        rx.button(
                            "Demo local",
                            on_click=StudyState.start_practica_local,
                            variant="soft",
                            color_scheme="gray",
                            flex="1",
                        ),
                        width="100%",
                        spacing="2",
                    ),
                    rx.button(
                        "Generar práctica",
                        on_click=StudyState.start_practica,
                        loading=StudyState.loading,
                        width="100%",
                        size="3",
                        color_scheme="blue",
                        high_contrast=True,
                    ),
                    rx.button(
                        "Practicar debilidades",
                        on_click=StudyState.start_practica_debilidades,
                        loading=StudyState.loading,
                        width="100%",
                        size="3",
                        variant="soft",
                        color_scheme="blue",
                    ),
                    spacing="3",
                    width="100%",
                    align="start",
                ),
                padding="1.25rem",
            ),
        ),
    )


def simulacro_tab() -> rx.Component:
    return rx.cond(
        (StudyState.mode == "simulacro") & (StudyState.phase == "quiz"),
        quiz_panel(),
        rx.cond(
            (StudyState.mode == "simulacro") & (StudyState.phase == "results"),
            results_panel(),
            _card(
                rx.vstack(
                    rx.heading("Simulacro Oficial UNA", size="6"),
                    rx.text(
                        "60 preguntas · muestreo estratificado · sin feedback en vivo "
                        "(integridad del cronómetro).",
                        color=COLORS["muted"],
                        size="2",
                    ),
                    rx.cond(
                        StudyState.error_msg != "",
                        rx.callout(
                            StudyState.error_msg,
                            icon="triangle-alert",
                            color_scheme="red",
                            width="100%",
                        ),
                    ),
                    rx.select(
                        ["todo", "oficial", "mis_guias"],
                        value=StudyState.fuente_banco,
                        on_change=StudyState.set_fuente,
                        width="100%",
                    ),
                    rx.button(
                        "Iniciar simulacro",
                        on_click=StudyState.start_simulacro,
                        loading=StudyState.loading,
                        width="100%",
                        size="3",
                        color_scheme="blue",
                        high_contrast=True,
                    ),
                    spacing="3",
                    width="100%",
                ),
                padding="1.25rem",
            ),
        ),
    )


def _pending_item_card(it: rx.Var) -> rx.Component:
    uid = it["uid"]
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.badge(it["materia_nombre"], color_scheme="blue", variant="soft"),
                rx.badge(it["tag_tematico"], color_scheme="gray", variant="outline"),
                rx.badge(it["nivel_estimado"], color_scheme="amber", variant="soft"),
                rx.cond(
                    it["rango_origen"] != "",
                    rx.badge(
                        it["rango_origen"],
                        color_scheme="indigo",
                        variant="soft",
                    ),
                ),
                spacing="2",
                wrap="wrap",
                width="100%",
            ),
            rx.text(it["enunciado"], size="3", weight="medium", color=COLORS["ink"]),
            rx.vstack(
                rx.text("A) ", it["alt_a"], size="2", color=COLORS["slate"]),
                rx.text("B) ", it["alt_b"], size="2", color=COLORS["slate"]),
                rx.text("C) ", it["alt_c"], size="2", color=COLORS["slate"]),
                rx.text("D) ", it["alt_d"], size="2", color=COLORS["slate"]),
                rx.text("E) ", it["alt_e"], size="2", color=COLORS["slate"]),
                spacing="1",
                width="100%",
                align="start",
            ),
            rx.text(
                "Correcta: ",
                it["alternativa_correcta"],
                size="2",
                weight="bold",
                color=COLORS["success"],
            ),
            rx.hstack(
                rx.button(
                    "Editar",
                    size="1",
                    variant="soft",
                    on_click=IngestState.start_edit_item(uid),
                ),
                rx.button(
                    "Eliminar",
                    size="1",
                    variant="soft",
                    color_scheme="red",
                    on_click=IngestState.delete_pending_item(uid),
                ),
                spacing="2",
            ),
            rx.cond(
                IngestState.editing_uid == uid,
                rx.vstack(
                    rx.text("Edición rápida", size="2", weight="bold"),
                    rx.input(
                        value=IngestState.edit_enunciado,
                        on_change=IngestState.set_edit_enunciado,
                        placeholder="Enunciado",
                        width="100%",
                    ),
                    rx.select(
                        IngestState.materias_options,
                        value=IngestState.edit_materia,
                        on_change=IngestState.set_edit_materia,
                        placeholder="Re-clasificar materia",
                        width="100%",
                    ),
                    rx.input(
                        value=IngestState.edit_tema,
                        on_change=IngestState.set_edit_tema,
                        placeholder="Tema",
                        width="100%",
                    ),
                    rx.input(
                        value=IngestState.edit_tag,
                        on_change=IngestState.set_edit_tag,
                        placeholder="Tag temático",
                        width="100%",
                    ),
                    rx.select(
                        ["basica", "intermedia", "avanzada"],
                        value=IngestState.edit_nivel,
                        on_change=IngestState.set_edit_nivel,
                        width="100%",
                    ),
                    rx.input(value=IngestState.edit_a, on_change=IngestState.set_edit_a, placeholder="A", width="100%"),
                    rx.input(value=IngestState.edit_b, on_change=IngestState.set_edit_b, placeholder="B", width="100%"),
                    rx.input(value=IngestState.edit_c, on_change=IngestState.set_edit_c, placeholder="C", width="100%"),
                    rx.input(value=IngestState.edit_d, on_change=IngestState.set_edit_d, placeholder="D", width="100%"),
                    rx.input(value=IngestState.edit_e, on_change=IngestState.set_edit_e, placeholder="E", width="100%"),
                    rx.select(
                        ["A", "B", "C", "D", "E"],
                        value=IngestState.edit_correcta,
                        on_change=IngestState.set_edit_correcta,
                        width="100%",
                    ),
                    rx.text_area(
                        value=IngestState.edit_justificacion,
                        on_change=IngestState.set_edit_justificacion,
                        placeholder="Justificación",
                        width="100%",
                    ),
                    rx.hstack(
                        rx.button("Cancelar", variant="soft", on_click=IngestState.cancel_edit),
                        rx.button(
                            "Guardar cambios",
                            color_scheme="blue",
                            on_click=IngestState.save_edit_item,
                        ),
                        spacing="2",
                    ),
                    spacing="2",
                    width="100%",
                    padding="0.75rem",
                    background=COLORS["brand_soft"],
                    border_radius="12px",
                ),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        padding="1rem",
        border=f"1px solid {COLORS['line']}",
        border_radius="14px",
        background=COLORS["surface"],
        width="100%",
    )


def ingest_tab(*, admin: bool = False) -> rx.Component:
    title = "Banco Oficial (Admin)" if admin else "Mis Guías"
    caption = (
        "PDF/imágenes → revisión → bóveda oficial."
        if admin
        else "Sube → multi-rango → IA por densidad → revisa y guarda a tu bóveda."
    )
    upload_id = "ingest_upload_admin" if admin else "ingest_upload_student"
    on_enter = (
        IngestState.set_mode_admin if admin else IngestState.set_mode_student
    )
    return _card(
        rx.vstack(
            rx.heading(title, size="6"),
            rx.text(caption, color=COLORS["muted"], size="2"),
            rx.button(
                "Activar este módulo",
                on_click=on_enter,
                variant="soft",
                size="1",
                width="100%",
            ),
            rx.upload(
                rx.vstack(
                    rx.icon("upload", size=22, color=COLORS["brand"]),
                    rx.text("Arrastra o selecciona archivos", weight="medium", size="2"),
                    rx.text(
                        "PDF, PNG, JPG · máx. 3 · se detectan las páginas del PDF",
                        size="1",
                        color=COLORS["muted"],
                        text_align="center",
                    ),
                    align="center",
                    spacing="2",
                ),
                id=upload_id,
                accept={
                    "application/pdf": [".pdf"],
                    "image/png": [".png"],
                    "image/jpeg": [".jpg", ".jpeg"],
                },
                max_files=3,
                on_drop=IngestState.prepare_files(
                    rx.upload_files(upload_id=upload_id)
                ),
                border=f"1px dashed {COLORS['line']}",
                padding="1.25rem",
                width="100%",
                border_radius="14px",
                background=COLORS["brand_soft"],
            ),
            rx.button(
                "Cargar selección",
                on_click=IngestState.prepare_files(
                    rx.upload_files(upload_id=upload_id)
                ),
                loading=IngestState.loading,
                variant="soft",
                width="100%",
            ),
            rx.cond(
                IngestState.n_files > 0,
                rx.vstack(
                    rx.text("Documento activo", size="2", weight="bold"),
                    rx.foreach(
                        IngestState.file_meta,
                        lambda m: rx.text(m["label"], size="2"),
                    ),
                    rx.cond(
                        IngestState.has_pdf,
                        rx.callout(
                            IngestState.pdf_pages_banner,
                            icon="book-open",
                            color_scheme="blue",
                            width="100%",
                        ),
                    ),
                    spacing="2",
                    width="100%",
                ),
            ),
            rx.cond(
                IngestState.show_page_range,
                rx.vstack(
                    rx.text("Multi-rango de páginas", size="2", weight="bold"),
                    rx.hstack(
                        rx.vstack(
                            rx.text("Desde", size="1", color=COLORS["muted"]),
                            rx.input(
                                type="number",
                                value=IngestState.page_start,
                                on_change=IngestState.set_page_start,
                                width="100%",
                                size="3",
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        rx.vstack(
                            rx.text("Hasta", size="1", color=COLORS["muted"]),
                            rx.input(
                                type="number",
                                value=IngestState.page_end,
                                on_change=IngestState.set_page_end,
                                width="100%",
                                size="3",
                            ),
                            spacing="1",
                            width="100%",
                        ),
                        spacing="3",
                        width="100%",
                    ),
                    rx.cond(
                        IngestState.range_error != "",
                        rx.callout(
                            IngestState.range_error,
                            icon="triangle-alert",
                            color_scheme="red",
                            width="100%",
                        ),
                        rx.text(IngestState.range_label, size="2", color=COLORS["slate"]),
                    ),
                    rx.button(
                        "Agregar rango a la cola",
                        on_click=IngestState.add_page_range,
                        variant="soft",
                        color_scheme="blue",
                        width="100%",
                    ),
                    rx.foreach(
                        IngestState.page_ranges,
                        lambda r: rx.hstack(
                            rx.text(r["label"], size="2"),
                            rx.spacer(),
                            rx.icon_button(
                                rx.icon("x", size=14),
                                size="1",
                                variant="ghost",
                                color_scheme="red",
                                on_click=IngestState.remove_page_range(r["id"]),
                            ),
                            width="100%",
                            padding="0.5rem 0.75rem",
                            border=f"1px solid {COLORS['line']}",
                            border_radius="10px",
                        ),
                    ),
                    rx.text(
                        f"Rangos en cola: ",
                        IngestState.ranges_count,
                        size="1",
                        color=COLORS["muted"],
                    ),
                    spacing="3",
                    width="100%",
                ),
            ),
            rx.hstack(
                rx.button(
                    "Limpiar todo",
                    on_click=IngestState.clear_files,
                    variant="soft",
                    color_scheme="gray",
                ),
                rx.button(
                    "Agregar otro rango de este documento",
                    on_click=IngestState.keep_document_add_range,
                    variant="soft",
                    display=rx.cond(IngestState.n_files > 0, "inline-flex", "none"),
                ),
                spacing="2",
                width="100%",
                wrap="wrap",
            ),
            rx.button(
                "Analizar rangos (sin guardar aún)",
                on_click=IngestState.analyze_files,
                loading=IngestState.loading,
                disabled=~IngestState.can_analyze,
                color_scheme="blue",
                high_contrast=True,
                width="100%",
                size="3",
            ),
            rx.cond(
                IngestState.loading,
                rx.vstack(
                    rx.hstack(
                        rx.text(IngestState.progress_detail, size="2"),
                        rx.spacer(),
                        rx.text(IngestState.progress_pct, "%", weight="bold", color=COLORS["brand"]),
                        width="100%",
                    ),
                    rx.progress(
                        value=IngestState.progress_pct,
                        width="100%",
                        color_scheme="blue",
                        radius="full",
                    ),
                    spacing="2",
                    width="100%",
                ),
            ),
            rx.cond(
                IngestState.status != "",
                rx.callout(IngestState.status, icon="info", color_scheme="green", width="100%"),
            ),
            rx.cond(
                IngestState.error != "",
                rx.callout(
                    IngestState.error,
                    icon="triangle-alert",
                    color_scheme="red",
                    width="100%",
                ),
            ),
            # Revisión post-ingesta
            rx.cond(
                IngestState.show_review,
                rx.vstack(
                    rx.heading("Revisión de preguntas", size="5"),
                    rx.callout(
                        IngestState.area_summary,
                        icon="chart-pie",
                        color_scheme="indigo",
                        width="100%",
                    ),
                    rx.text(
                        IngestState.pending_count,
                        " ítem(s) pendientes de confirmar",
                        size="2",
                        color=COLORS["muted"],
                    ),
                    rx.foreach(IngestState.pending_items, _pending_item_card),
                    rx.button(
                        "Confirmar y guardar en bóveda",
                        on_click=IngestState.confirm_save_items,
                        loading=IngestState.loading,
                        color_scheme="green",
                        high_contrast=True,
                        width="100%",
                        size="3",
                    ),
                    spacing="3",
                    width="100%",
                    align="start",
                ),
            ),
            rx.cond(
                IngestState.last_warnings != "",
                rx.text(IngestState.last_warnings, size="1", color=COLORS["muted"]),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        padding="1.25rem",
    )



def _dash_header() -> rx.Component:
    return rx.hstack(
        rx.vstack(
            rx.heading(
                "Mi rendimiento",
                size="6",
                color=COLORS["ink"],
                letter_spacing="-0.02em",
            ),
            rx.text(
                "Tu brújula hacia Medicina · Índice, foco semanal y dominio por materia.",
                color=COLORS["muted"],
                size="2",
            ),
            spacing="1",
            align="start",
        ),
        rx.spacer(),
        rx.button(
            rx.icon("refresh-cw", size=16),
            "Actualizar",
            on_click=DashboardState.load(AuthState.usuario_id),
            variant="soft",
            size="2",
            color_scheme="blue",
        ),
        width="100%",
        align="start",
    )


def _dash_empty() -> rx.Component:
    return rx.vstack(
        rx.callout(
            "Aún no hay intentos. Completa una práctica o simulacro para armar tu Índice Medicina.",
            icon="info",
            color_scheme="blue",
            width="100%",
        ),
        rx.button(
            "Ir a Práctica enfocada",
            on_click=AppState.set_tab("practica"),
            color_scheme="blue",
            high_contrast=True,
            size="3",
        ),
        spacing="3",
        width="100%",
        align="start",
    )


def _metric_card(
    label: str,
    *body,
    accent: str | None = None,
) -> rx.Component:
    props: dict = {
        "padding": "1.1rem 1.2rem",
        "height": "100%",
    }
    if accent:
        props["border_top"] = f"3px solid {accent}"
    return _card(
        rx.vstack(
            rx.text(
                label,
                size="1",
                weight="medium",
                color=COLORS["muted"],
                letter_spacing="0.04em",
                text_transform="uppercase",
            ),
            *body,
            spacing="2",
            align="start",
            width="100%",
        ),
        **props,
    )


def _dash_hero_metrics() -> rx.Component:
    tendencia_icon = rx.cond(
        DashboardState.tendencia == "up",
        rx.icon("trending-up", size=22, color=COLORS["success"]),
        rx.cond(
            DashboardState.tendencia == "down",
            rx.icon("trending-down", size=22, color=COLORS["danger"]),
            rx.cond(
                DashboardState.tendencia == "flat",
                rx.icon("minus", size=22, color=COLORS["muted"]),
                rx.icon("activity", size=22, color=COLORS["muted"]),
            ),
        ),
    )
    tendencia_color = rx.cond(
        DashboardState.tendencia == "up",
        COLORS["success"],
        rx.cond(
            DashboardState.tendencia == "down",
            COLORS["danger"],
            COLORS["ink"],
        ),
    )
    return rx.vstack(
        rx.text(
            DashboardState.estado,
            size="2",
            weight="medium",
            color=COLORS["slate"],
        ),
        rx.box(
            _metric_card(
                "Índice Medicina",
                rx.hstack(
                    rx.heading(
                        DashboardState.indice,
                        size="8",
                        color=COLORS["brand"],
                        letter_spacing="-0.03em",
                    ),
                    rx.badge(
                        DashboardState.banda,
                        color_scheme="blue",
                        variant="soft",
                        size="2",
                    ),
                    spacing="3",
                    align="center",
                ),
                rx.text(DashboardState.frase, size="1", color=COLORS["muted"]),
                accent=COLORS["brand"],
            ),
            _metric_card(
                "Intentos registrados",
                rx.heading(
                    DashboardState.total_intentos,
                    size="8",
                    color=COLORS["ink"],
                    letter_spacing="-0.03em",
                ),
                rx.text(
                    DashboardState.precision_pct,
                    "% de precisión global",
                    size="1",
                    color=COLORS["muted"],
                ),
                accent=COLORS["slate"],
            ),
            _metric_card(
                "Tendencia semanal",
                rx.hstack(
                    tendencia_icon,
                    rx.heading(
                        DashboardState.tendencia_label,
                        size="5",
                        color=tendencia_color,
                        letter_spacing="-0.02em",
                    ),
                    spacing="2",
                    align="center",
                ),
                rx.text(
                    "Comparado con los 7 días previos",
                    size="1",
                    color=COLORS["muted"],
                ),
                accent=COLORS["success"],
            ),
            display="grid",
            grid_template_columns=rx.breakpoints(
                initial="1fr",
                md="repeat(3, 1fr)",
            ),
            gap="0.9rem",
            width="100%",
        ),
        spacing="3",
        width="100%",
        align="start",
    )


def _dash_mission_banner() -> rx.Component:
    return rx.cond(
        DashboardState.has_mision,
        _card(
            rx.hstack(
                rx.center(
                    rx.icon("target", size=22, color=COLORS["warning"]),
                    width="48px",
                    height="48px",
                    border_radius="14px",
                    background=COLORS["warning_bg"],
                    border=f"1px solid {COLORS['warning_border']}",
                    flex_shrink="0",
                ),
                rx.vstack(
                    rx.text(
                        "Misión de la semana",
                        size="1",
                        weight="bold",
                        color=COLORS["warning"],
                        letter_spacing="0.04em",
                        text_transform="uppercase",
                    ),
                    rx.hstack(
                        rx.text("Tu foco de esta semana:", size="3", color=COLORS["ink"]),
                        rx.text(
                            DashboardState.mision_tema,
                            size="3",
                            weight="bold",
                            color=COLORS["ink"],
                        ),
                        spacing="1",
                        flex_wrap="wrap",
                    ),
                    rx.text(
                        "¡Súbelo del ",
                        DashboardState.mision_desde,
                        "% al ",
                        DashboardState.mision_hasta,
                        "%!",
                        size="2",
                        color=COLORS["slate"],
                    ),
                    rx.cond(
                        DashboardState.mision_materia != "",
                        rx.text(
                            DashboardState.mision_materia,
                            size="1",
                            color=COLORS["muted"],
                        ),
                    ),
                    spacing="1",
                    align="start",
                    flex="1",
                    min_width="0",
                ),
                rx.button(
                    rx.icon("dumbbell", size=16),
                    "Practicar este tema",
                    on_click=AppState.set_tab("practica"),
                    color_scheme="amber",
                    high_contrast=True,
                    size="3",
                    flex_shrink="0",
                ),
                width="100%",
                align="center",
                spacing="4",
                flex_wrap="wrap",
            ),
            padding="1.15rem 1.25rem",
            background=COLORS["warning_bg"],
            border=f"1px solid {COLORS['warning_border']}",
        ),
        rx.fragment(),
    )


def _materia_row(m: rx.Var) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.text(m["nombre"], size="2", weight="medium", color=COLORS["ink"]),
            rx.spacer(),
            rx.badge(m["dominio"], color_scheme=m["color_scheme"], variant="soft"),
            width="100%",
            align="center",
        ),
        rx.progress(
            value=m["dominio_score"],
            width="100%",
            color_scheme=m["color_scheme"],
            radius="full",
            size="2",
        ),
        rx.hstack(
            rx.text(
                m["precision_label"],
                " precisión",
                size="1",
                color=COLORS["muted"],
            ),
            rx.spacer(),
            rx.text(
                m["intentos"],
                " intentos",
                size="1",
                color=COLORS["muted"],
            ),
            width="100%",
        ),
        spacing="1",
        width="100%",
        padding_y="0.55rem",
        border_bottom=f"1px solid {COLORS['line']}",
    )


def _dash_materias() -> rx.Component:
    return _card(
        rx.vstack(
            rx.hstack(
                rx.icon("layers", size=18, color=COLORS["brand"]),
                rx.heading("Dominio por materia", size="4", color=COLORS["ink"]),
                spacing="2",
                align="center",
            ),
            rx.text(
                "Barras según precisión: priorizar · mejorar · fuerte.",
                size="1",
                color=COLORS["muted"],
            ),
            rx.cond(
                DashboardState.materias_resumen.length() > 0,
                rx.vstack(
                    rx.foreach(DashboardState.materias_resumen, _materia_row),
                    spacing="0",
                    width="100%",
                ),
                rx.text(
                    "Cuando practiques, aquí verás el dominio por materia.",
                    size="2",
                    color=COLORS["muted"],
                ),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        padding="1.15rem 1.25rem",
        height="100%",
    )


def _actividad_row(a: rx.Var) -> rx.Component:
    return rx.hstack(
        rx.box(
            width="4px",
            height="100%",
            min_height="40px",
            border_radius="4px",
            background=rx.cond(
                a["color_scheme"] == "green",
                COLORS["success"],
                rx.cond(
                    a["color_scheme"] == "amber",
                    COLORS["warning"],
                    COLORS["danger"],
                ),
            ),
            flex_shrink="0",
        ),
        rx.vstack(
            rx.text(a["tema"], size="2", weight="medium", color=COLORS["ink"]),
            rx.hstack(
                rx.text(a["materia"], size="1", color=COLORS["muted"]),
                rx.text("·", size="1", color=COLORS["muted"]),
                rx.text(a["cuando"], size="1", color=COLORS["muted"]),
                spacing="1",
                flex_wrap="wrap",
            ),
            rx.hstack(
                rx.badge(
                    a["precision_label"],
                    color_scheme=a["color_scheme"],
                    variant="soft",
                    size="1",
                ),
                rx.text(
                    a["intentos"],
                    " intentos",
                    size="1",
                    color=COLORS["muted"],
                ),
                spacing="2",
                align="center",
            ),
            spacing="1",
            align="start",
            flex="1",
            min_width="0",
        ),
        width="100%",
        align="stretch",
        spacing="3",
        padding_y="0.5rem",
        border_bottom=f"1px solid {COLORS['line']}",
    )


def _dash_actividad() -> rx.Component:
    return _card(
        rx.vstack(
            rx.hstack(
                rx.icon("history", size=18, color=COLORS["brand"]),
                rx.heading("Última actividad", size="4", color=COLORS["ink"]),
                spacing="2",
                align="center",
            ),
            rx.text(
                "Temas que practicaste más recientemente.",
                size="1",
                color=COLORS["muted"],
            ),
            rx.cond(
                DashboardState.actividad_reciente.length() > 0,
                rx.vstack(
                    rx.foreach(DashboardState.actividad_reciente, _actividad_row),
                    spacing="0",
                    width="100%",
                ),
                rx.text(
                    "Tu cronología aparecerá aquí tras la primera práctica.",
                    size="2",
                    color=COLORS["muted"],
                ),
            ),
            spacing="3",
            width="100%",
            align="start",
        ),
        padding="1.15rem 1.25rem",
        height="100%",
    )


def dashboard_tab() -> rx.Component:
    return _card(
        rx.vstack(
            _dash_header(),
            rx.cond(
                DashboardState.error != "",
                rx.callout(
                    DashboardState.error,
                    icon="triangle-alert",
                    color_scheme="red",
                    width="100%",
                ),
            ),
            rx.cond(
                DashboardState.empty,
                _dash_empty(),
                rx.vstack(
                    _dash_hero_metrics(),
                    _dash_mission_banner(),
                    rx.box(
                        _dash_materias(),
                        _dash_actividad(),
                        display="grid",
                        grid_template_columns=rx.breakpoints(
                            initial="1fr",
                            md="1.6fr 1fr",
                        ),
                        gap="0.9rem",
                        width="100%",
                        align_items="start",
                    ),
                    spacing="4",
                    width="100%",
                    align="start",
                ),
            ),
            spacing="4",
            width="100%",
            align="start",
        ),
        padding="1.35rem",
    )


def authenticated_view() -> rx.Component:
    body = rx.match(
        AppState.tab,
        ("guias", ingest_tab(admin=False)),
        ("practica", practica_tab()),
        ("simulacro", simulacro_tab()),
        ("admin", ingest_tab(admin=True)),
        ("dashboard", dashboard_tab()),
        practica_tab(),
    )
    return app_shell(body)


def root_view() -> rx.Component:
    return rx.fragment(
        rx.cond(
            AuthState.is_authenticated,
            authenticated_view(),
            auth_view(),
        )
    )
