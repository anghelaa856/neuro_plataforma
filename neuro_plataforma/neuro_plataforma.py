"""Neuro Plataforma — entry point Reflex (producto completo)."""

import reflex as rx

from neuro_plataforma.components import root_view
from neuro_plataforma.state import (  # noqa: F401
    AppState,
    AuthState,
    DashboardState,
    IngestState,
    StudyState,
)


def index() -> rx.Component:
    return root_view()


app = rx.App()
app.add_page(
    index,
    route="/",
    title="Neuro Plataforma · UNA Medicina",
    description=(
        "Práctica enfocada, simulacro oficial, Mis Guías "
        "e Índice Medicina — Reflex."
    ),
    on_load=AuthState.on_load,
)
