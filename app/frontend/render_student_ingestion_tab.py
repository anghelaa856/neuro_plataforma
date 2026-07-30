"""
Ingesta self-service del estudiante: «Generador de Simulacros con tu Guía».

Soporta PDF (chunks de texto) y fotos JPG/PNG del compendio físico (visión multimodal).
Flujo: 1) Sube guía o foto  2) La IA extrae preguntas A–E y clasifica por materia/tema.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import streamlit as st
from pypdf import PdfReader
from io import BytesIO

from app.infrastructure.database.banco_repository import banco_repository
from app.services.banco_extraction_service import (
    extract_banco_preguntas_from_chunks,
    extract_banco_preguntas_from_images,
)
from app.services.pdf_processor import process_pdf

# Umbral a partir del cual pedimos acotar páginas
PAGINAS_LARGAS = 20
MAX_PAGINAS_DEFAULT = 30
MAX_PAGINAS_HARD = 80

_IMAGE_EXTS = {"png", "jpg", "jpeg"}
_PDF_EXTS = {"pdf"}


def _count_pdf_pages(file_bytes: bytes) -> int:
    try:
        return len(PdfReader(BytesIO(file_bytes)).pages)
    except Exception:
        return 0


def _file_ext(name: Optional[str]) -> str:
    if not name or "." not in name:
        return ""
    return str(name).rsplit(".", 1)[-1].strip().lower()


def _mostrar_resultado_ingesta(result: Any) -> None:
    st.session_state["student_ingesta_last"] = result.to_dict()
    pers = result.persistencia or {}
    materias_tocadas = pers.get("materias_tocadas") or {}
    detalle_mats = (
        ", ".join(f"{k}: {v}" for k, v in materias_tocadas.items())
        if materias_tocadas
        else "ninguna"
    )
    st.success(
        f"Se agregaron **{pers.get('n_insertadas', 0)}** preguntas privadas · "
        f"fragmentos={result.chunks_procesados} · "
        f"duplicadas={pers.get('n_duplicadas', 0)} · "
        f"temas={pers.get('temas_upserted', 0)}. "
        f"Distribución por materia: {detalle_mats}. "
        "Practica con fuente «Solo Mis Guías» o «Todo»."
    )
    if result.warnings:
        with st.expander("Avisos del proceso (delta / densidad / clasificación)"):
            for w in result.warnings:
                st.write(f"- {w}")


def render_student_ingestion_tab(*, usuario_id: int) -> None:
    st.subheader("📚 Generador de Simulacros con tu Guía")
    st.caption(
        "**1.** Sube tu guía en **PDF** o una **foto** (JPG/PNG) del compendio físico.  "
        "**2.** La IA extrae preguntas A–E privadas, las clasifica en la materia "
        "oficial correcta y omite temas que ya tienes."
    )

    uid = int(usuario_id)
    try:
        materias = banco_repository.fetch_materias()
    except Exception as exc:
        st.error(f"No se pudo leer el catálogo de materias: {exc}")
        return

    if not materias:
        st.warning("Catálogo vacío. Contacta al administrador para sembrar materias UNA.")
        return

    with st.expander("¿Qué materias reconoce la IA?", expanded=False):
        st.write(", ".join(f"{int(m['codigo']):02d}. {m['nombre']}" for m in materias))

    uploaded = st.file_uploader(
        "PDF o foto de tu guía / compendio",
        type=["pdf", "png", "jpg", "jpeg"],
        key="student_ingesta_upload",
        help=(
            "PDF: se parte en fragmentos de texto. "
            "Foto: la IA lee la imagen (ideal para compendios físicos)."
        ),
    )

    max_pages: Optional[int] = None
    page_count = 0
    ext = _file_ext(getattr(uploaded, "name", None) if uploaded else None)
    is_pdf = ext in _PDF_EXTS
    is_image = ext in _IMAGE_EXTS

    if uploaded is not None and is_pdf:
        page_count = _count_pdf_pages(uploaded.getvalue())
        if page_count > PAGINAS_LARGAS:
            st.warning(
                f"Tu PDF tiene **{page_count} páginas**. "
                "Para no saturar la IA, elige cuántas procesar en esta pasada."
            )
            max_pages = st.slider(
                "Procesar primeras N páginas",
                min_value=5,
                max_value=min(MAX_PAGINAS_HARD, max(page_count, 5)),
                value=min(MAX_PAGINAS_DEFAULT, page_count),
                key="student_ingesta_max_pages",
            )
        elif page_count > 0:
            st.caption(f"Documento: **{page_count}** páginas · se analizará completo.")
    elif uploaded is not None and is_image:
        st.caption(
            "Modo foto: la IA leerá el texto visible de la imagen "
            "(enfoca bien, buena luz, una página por foto)."
        )
        try:
            st.image(uploaded.getvalue(), caption=getattr(uploaded, "name", "foto"), width=360)
        except Exception:
            pass

    if st.button(
        "Analizar mi guía con IA",
        type="primary",
        disabled=uploaded is None,
        use_container_width=True,
        key="student_ingesta_run",
    ):
        if uploaded is None:
            st.error("Sube un PDF o una foto primero.")
            return

        file_bytes = uploaded.getvalue()
        nombre_archivo = getattr(uploaded, "name", None) or (
            "mi_guia.pdf" if is_pdf else "mi_compendio.jpg"
        )
        progress = st.progress(0, text="Preparando tu guía…")
        status = st.empty()

        try:
            if is_pdf:
                status.info("Extrayendo y limpiando el PDF…")
                progress.progress(5, text="Aduana PDF…")
                doc = process_pdf(
                    file_bytes,
                    allow_ocr=True,
                    source_filename=nombre_archivo,
                    max_pages=max_pages,
                )
                if not doc.text or not doc.chunks:
                    progress.empty()
                    status.empty()
                    st.error("No pudimos leer texto útil del PDF. ¿Está escaneado sin OCR?")
                    for w in doc.warnings:
                        st.caption(f"⚠️ {w}")
                    return

                nombre_archivo = (
                    (doc.meta or {}).get("nombre_archivo_fuente")
                    or nombre_archivo
                )
                pages_proc = (doc.meta or {}).get("pages_procesadas") or doc.page_count
                st.info(
                    f"Guía lista · `{nombre_archivo}` · "
                    f"{pages_proc}/{doc.page_count} págs · "
                    f"**{len(doc.chunks)} fragmentos** · método `{doc.method}`"
                )
                for w in doc.warnings:
                    st.caption(f"⚠️ {w}")

                def _on_chunk(idx: int, total: int, msg: str) -> None:
                    ratio = min(1.0, (idx + 1) / max(total, 1)) if total else 1.0
                    if idx >= total:
                        ratio = 1.0
                    progress.progress(min(0.95, 0.08 + 0.85 * ratio), text=msg)
                    status.write(msg)

                result = extract_banco_preguntas_from_chunks(
                    doc.chunks,
                    materia_id=None,
                    auto_clasificar=True,
                    origen_contenido="pdf",
                    fuente="openrouter",
                    nombre_archivo_fuente=str(nombre_archivo),
                    propietario_usuario_id=uid,
                    persist=True,
                    pause_between_chunks_s=0.4,
                    on_chunk_progress=_on_chunk,
                )
            elif is_image:
                status.info("Preparando la foto para la IA multimodal…")
                progress.progress(10, text="Codificando imagen…")

                def _on_img(idx: int, total: int, msg: str) -> None:
                    ratio = min(1.0, (idx + 1) / max(total, 1)) if total else 1.0
                    if idx >= total:
                        ratio = 1.0
                    progress.progress(min(0.95, 0.12 + 0.80 * ratio), text=msg)
                    status.write(msg)

                st.info(
                    f"Foto lista · `{nombre_archivo}` · "
                    "la IA leerá el contenido visible y clasificará por materia."
                )
                result = extract_banco_preguntas_from_images(
                    [{"bytes": file_bytes, "nombre": str(nombre_archivo)}],
                    materia_id=None,
                    auto_clasificar=True,
                    origen_contenido="imagen",
                    fuente="openrouter",
                    nombre_archivo_fuente=str(nombre_archivo),
                    propietario_usuario_id=uid,
                    persist=True,
                    pause_between_images_s=0.4,
                    on_image_progress=_on_img,
                )
            else:
                progress.empty()
                status.empty()
                st.error(
                    f"Formato no soportado (.{ext or '?'}). "
                    "Usa PDF, PNG, JPG o JPEG."
                )
                return

            progress.progress(100, text="¡Listo! Preguntas guardadas en tu bóveda privada.")
            status.success("Análisis completado.")
            _mostrar_resultado_ingesta(result)
        except Exception as exc:
            st.error(f"No se pudo procesar tu guía: {exc}")

    last: Optional[Dict[str, Any]] = st.session_state.get("student_ingesta_last")
    if last:
        with st.expander("Última carga (detalle)"):
            st.json(last)
