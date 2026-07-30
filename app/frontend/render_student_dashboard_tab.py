"""
Panel «Mi rendimiento» — brújula motivacional hacia Medicina (UNA Biomédicas).

Vista limpia: Índice Medicina → plan semanal → materias → detalle bajo demanda.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from app.infrastructure.database.historial_repository import historial_repository
from app.services.student_dashboard_service import (
    META_MEDICINA_MIN,
    calcular_indice_medicina,
    construir_plan_semanal,
    etiqueta_trayectoria,
    resumen_por_materia,
)
from app.services.tutor_engine import BancoInsuficienteError, tutor_engine


def render_student_dashboard_tab(
    *,
    usuario_id: int,
    nombre_alumno: str = "",
) -> None:
    """Dashboard de progreso filtrado estrictamente por usuario_id."""
    nombre = (nombre_alumno or "").strip()
    titulo = f"Camino hacia Medicina · {nombre}" if nombre else "Camino hacia Medicina"
    st.subheader(titulo)
    st.caption("Tu brújula de estudio: qué tan cerca estás y qué practicar hoy.")

    try:
        kpis = historial_repository.fetch_kpis_alumno(int(usuario_id))
        por_tema = historial_repository.fetch_rendimiento_por_tema(int(usuario_id))
        ventanas = historial_repository.fetch_precision_ventanas(int(usuario_id), dias=7)
    except Exception as exc:
        st.error(f"No se pudo cargar tu historial: {exc}")
        return

    total = int(kpis.get("total_intentos") or 0)
    if total == 0:
        st.info(
            "Aún no hay intentos registrados. Completa una **Práctica Enfocada** "
            "o un **Simulacro** para armar tu Índice Medicina y tu plan de la semana."
        )
        return

    indice = calcular_indice_medicina(
        precision_pct=float(kpis.get("precision_pct") or 0),
        total_intentos=total,
        precision_7d=ventanas.get("precision_7d"),
        precision_7d_prev=ventanas.get("precision_7d_prev"),
    )
    plan = construir_plan_semanal(por_tema, min_intentos=3)
    materias = resumen_por_materia(por_tema)

    _render_brujula(indice, nombre=nombre, plan=plan)
    st.divider()
    _render_plan_semanal(plan, usuario_id=int(usuario_id))
    st.divider()
    _render_materias(materias)
    _render_numeros_detras(kpis, por_tema, indice)


# ---------------------------------------------------------------------------
# Sección superior — Brújula / Índice Medicina
# ---------------------------------------------------------------------------
def _render_brujula(
    indice: Dict[str, Any],
    *,
    nombre: str,
    plan: Dict[str, Any],
) -> None:
    with st.container():
        cuello = plan.get("cuello_botella")
        foco = ""
        if cuello:
            foco = f" · hoy tu foco es **{cuello.get('tema_nombre', 'tu cuello de botella')}**"

        st.markdown(f"**{indice['estado']}**{foco}")

        delta = indice.get("delta_semanal")
        delta_args: Dict[str, Any] = {}
        if delta is not None:
            delta_args["delta"] = f"{delta:+.1f} pts esta semana"

        c1, c2 = st.columns([1, 2])
        with c1:
            st.metric(
                "Índice Medicina",
                f"{float(indice['indice']):.0f}",
                **delta_args,
                help=(
                    "Precisión sostenida (aciertos ÷ decididos). "
                    f"Meta Medicina: {META_MEDICINA_MIN:.0f}–90%."
                ),
            )
        with c2:
            st.caption(
                "Base (0–69) · Competitiva (70–84) · "
                f"Meta Medicina ({META_MEDICINA_MIN:.0f}–100)"
            )
            st.progress(min(1.0, max(0.0, float(indice["indice"]) / 100.0)))
            st.caption(f"Banda actual: **{indice['banda']}**")

        if indice.get("muestra_temprana"):
            st.warning(indice["frase_pronostico"])
        elif indice["banda"] == "Meta Medicina":
            st.success(indice["frase_pronostico"])
        elif indice["banda"] == "Competitiva":
            st.info(indice["frase_pronostico"])
        else:
            st.info(indice["frase_pronostico"])

        mision = plan.get("mision")
        if mision:
            st.caption(mision["texto"])


# ---------------------------------------------------------------------------
# Sección media — Plan de esta semana
# ---------------------------------------------------------------------------
def _render_plan_semanal(plan: Dict[str, Any], *, usuario_id: int) -> None:
    st.markdown("#### Plan de esta semana")

    if plan.get("vacio"):
        st.success(
            "Empieza a practicar: con al menos unos intentos por tema "
            "armamos tu cuello de botella y tu misión semanal."
        )
        return

    slots = [
        ("① Cuello de botella", plan.get("cuello_botella"), "Priorizar"),
        ("② Refuerzo rápido", plan.get("refuerzo"), "Mejorar"),
        ("③ Mantener ritmo", plan.get("mantener"), "Fuerte"),
    ]
    cols = st.columns(3)
    for col, (titulo, item, rol_default) in zip(cols, slots):
        with col:
            _render_slot_prioridad(titulo, item, rol_default)

    cuello = plan.get("cuello_botella")
    if cuello is None:
        return

    st.write("")
    if st.button(
        "🎯 Atacar mi cuello de botella",
        type="primary",
        use_container_width=True,
        key="dashboard_btn_atacar_cuello",
    ):
        _lanzar_practica_cuello(usuario_id=usuario_id, cuello=cuello)


def _render_slot_prioridad(
    titulo: str,
    item: Optional[Dict[str, Any]],
    rol_default: str,
) -> None:
    with st.container(border=True):
        st.markdown(f"**{titulo}**")
        if not item:
            st.caption("Sin dato aún en este slot.")
            return

        precis = float(item.get("precision_pct") or 0)
        etiqueta = etiqueta_trayectoria(precis)
        tema = str(item.get("tema_nombre") or "Tema")
        materia = str(item.get("materia_nombre") or "")
        n = int(item.get("n_intentos") or 0)

        st.markdown(f"**{tema}**")
        st.caption(f"{materia} · {etiqueta}")
        st.progress(min(1.0, max(0.0, precis / 100.0)))
        st.caption(f"{precis:.0f}% de precisión · {n} intento(s)")

        if rol_default == "Priorizar":
            st.caption("Es lo que más frena tu Índice Medicina.")
        elif rol_default == "Mejorar":
            st.caption("Cierra este hueco con una práctica corta.")
        else:
            st.caption("Ya lo dominas: repaso breve para no perderlo.")


def _lanzar_practica_cuello(*, usuario_id: int, cuello: Dict[str, Any]) -> None:
    """Genera práctica del tema cuello (misma activación que Práctica Enfocada)."""
    tema_id = cuello.get("id_tema")
    materia_id = cuello.get("id_materia")
    tema_nombre = str(cuello.get("tema_nombre") or "tema prioritario")

    try:
        with st.spinner(f"Armando práctica de «{tema_nombre}»..."):
            if tema_id is not None:
                bloque = tutor_engine.generar_practica_enfocada(
                    usuario_id=int(usuario_id),
                    materia_id=int(materia_id) if materia_id is not None else None,
                    tema_id=int(tema_id),
                    limite=12,
                    fuente_banco="todo",
                )
            else:
                # Fallback: misma lógica que «Práctica de mis Debilidades».
                bloque = tutor_engine.generar_practica_debilidades(
                    usuario_id=int(usuario_id),
                    limite=12,
                    top_temas=3,
                    min_intentos=2,
                    fuente_banco="todo",
                )
        _activate_practica_bloque(bloque)
        st.success(
            f"Práctica lista · {bloque.total_preguntas} ítems de «{tema_nombre}». "
            "Abrí la pestaña **Práctica Enfocada** — el bloque ya está cargado."
        )
        st.rerun()
    except (BancoInsuficienteError, ValueError) as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"No se pudo generar la práctica del cuello de botella: {exc}")


def _activate_practica_bloque(bloque: Any) -> None:
    """Activa el bloque en session_state (mismo contrato que la pestaña Práctica)."""
    st.session_state["practica_activa"] = bloque
    st.session_state["practica_index"] = 0
    st.session_state["practica_respuestas"] = {}
    st.session_state["practica_tiempos_ms"] = {}
    st.session_state["practica_resultado"] = None
    st.session_state["practica_cerrada"] = False
    st.session_state["practica_index__review"] = 0
    st.session_state["practica_respuestas__comprobadas"] = {}
    st.session_state["practica_respuestas__check_ok"] = {}
    chats: Dict[str, Any] = dict(st.session_state.get("socratic_chats") or {})
    prefix = "practica:"
    st.session_state["socratic_chats"] = {
        k: v for k, v in chats.items() if not str(k).startswith(prefix)
    }


# ---------------------------------------------------------------------------
# Sección inferior — Materias (resumen limpio)
# ---------------------------------------------------------------------------
def _render_materias(materias: List[Dict[str, Any]]) -> None:
    st.markdown("#### Materias (resumen)")
    if not materias:
        st.caption("Cuando practiques, aquí verás el estado por materia.")
        return

    for m in materias:
        precis = float(m.get("precision_pct") or 0)
        etiqueta = str(m.get("etiqueta") or etiqueta_trayectoria(precis))
        nombre = str(m.get("materia_nombre") or "Materia")
        codigo = int(m.get("materia_codigo") or 0)

        label_col, bar_col, tag_col = st.columns([2, 3, 1])
        with label_col:
            st.markdown(f"**{codigo:02d} · {nombre}**")
        with bar_col:
            st.progress(min(1.0, max(0.0, precis / 100.0)))
            st.caption(f"{precis:.0f}%")
        with tag_col:
            if etiqueta == "Priorizar":
                st.warning(etiqueta)
            elif etiqueta == "Fuerte":
                st.success(etiqueta)
            else:
                st.info(etiqueta)


# ---------------------------------------------------------------------------
# Pie — Números detrás del índice (detalle denso colapsado)
# ---------------------------------------------------------------------------
def _render_numeros_detras(
    kpis: Dict[str, Any],
    por_tema: List[Dict[str, Any]],
    indice: Dict[str, Any],
) -> None:
    with st.expander("Números detrás del índice"):
        st.caption(
            "El Índice Medicina resume tu trayectoria; estos son los números base."
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Preguntas respondidas", f"{int(kpis.get('total_intentos') or 0)}")
        c2.metric("Precisión", f"{float(kpis.get('precision_pct') or 0):.1f}%")
        c3.metric(
            "Puntaje UNA proyectado",
            f"{int(kpis.get('puntaje_una_proyectado') or 0)}",
            help="Referencia secundaria (aciertos×10 − errores×2). No define la Meta Medicina.",
        )
        c4.metric("Índice Medicina", f"{float(indice['indice']):.0f}")

        d1, d2, d3 = st.columns(3)
        d1.write(f"**Aciertos:** {kpis.get('correctas', 0)}")
        d2.write(f"**Errores:** {kpis.get('incorrectas', 0)}")
        d3.write(f"**En blanco:** {kpis.get('en_blanco', 0)}")

        if not por_tema:
            return

        st.markdown("##### Detalle por tema")
        df = pd.DataFrame(por_tema)
        df["etiqueta"] = df["precision_pct"].map(etiqueta_trayectoria)
        df["fila"] = df.apply(
            lambda r: f"{int(r['materia_codigo']):02d} · {r['tema_nombre']}",
            axis=1,
        )

        chart_df = (
            df[["fila", "precision_pct"]]
            .set_index("fila")
            .rename(columns={"precision_pct": "% acierto"})
            .sort_values("% acierto", ascending=True)
        )
        st.bar_chart(chart_df, height=min(420, 80 + 28 * len(chart_df)))

        table = df[
            [
                "etiqueta",
                "materia_nombre",
                "tema_nombre",
                "n_intentos",
                "correctas",
                "incorrectas",
                "precision_pct",
            ]
        ].rename(
            columns={
                "etiqueta": "Señal",
                "materia_nombre": "Materia",
                "tema_nombre": "Tema",
                "n_intentos": "Intentos",
                "correctas": "Aciertos",
                "incorrectas": "Errores",
                "precision_pct": "% acierto",
            }
        )
        st.dataframe(
            table.sort_values("% acierto", ascending=True),
            use_container_width=True,
            hide_index=True,
            column_config={
                "% acierto": st.column_config.ProgressColumn(
                    "% acierto",
                    min_value=0,
                    max_value=100,
                    format="%.1f%%",
                ),
                "Señal": st.column_config.TextColumn("Señal", width="small"),
            },
        )
        st.caption(
            "Priorizar < 55% · Mejorar 55–84% · Fuerte ≥ 85% "
            "(sobre ítems marcados, sin blancos). Sin semáforo rojo."
        )
