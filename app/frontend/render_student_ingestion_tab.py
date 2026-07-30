"""
Ingesta self-service del estudiante: «Generador de Simulacros con tu Guía».

Soporta PDF y fotos JPG/PNG (una o varias). En móvil, los bytes se copian de
inmediato a session_state (lista) para no perderlos en el rerun de cámara/galería.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import streamlit as st
from pypdf import PdfReader
from io import BytesIO

from app.infrastructure.database.banco_repository import banco_repository
from app.services.banco_extraction_service import (
    BancoIngestionResult,
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

# Persistencia anti-pérdida en Android/Chrome (lote de archivos)
_SS_FILES = "student_ingesta_uploaded_files"  # List[{id, name, bytes}]
_SS_UPLOADER_NONCE = "student_ingesta_uploader_nonce"
# Claves legacy (archivo único) — se limpian al migrar/limpiar
_LEGACY_KEYS = (
    "student_ingesta_uploaded_file_bytes",
    "student_ingesta_uploaded_file_name",
    "student_ingesta_uploaded_file_id",
)


def _count_pdf_pages(file_bytes: bytes) -> int:
    try:
        return len(PdfReader(BytesIO(file_bytes)).pages)
    except Exception:
        return 0


def _file_ext(name: Optional[str]) -> str:
    if not name or "." not in name:
        return ""
    return str(name).rsplit(".", 1)[-1].strip().lower()


def _ensure_ingesta_session_defaults() -> None:
    if _SS_UPLOADER_NONCE not in st.session_state:
        st.session_state[_SS_UPLOADER_NONCE] = 0
    if _SS_FILES not in st.session_state:
        st.session_state[_SS_FILES] = []
    # Migración suave: un archivo suelto de la versión anterior → lista
    legacy_bytes = st.session_state.get("student_ingesta_uploaded_file_bytes")
    legacy_name = st.session_state.get("student_ingesta_uploaded_file_name")
    if legacy_bytes and not st.session_state[_SS_FILES]:
        name = str(legacy_name or "archivo")
        raw = bytes(legacy_bytes)
        fid = str(st.session_state.get("student_ingesta_uploaded_file_id") or f"{name}:{len(raw)}")
        st.session_state[_SS_FILES] = [{"id": fid, "name": name, "bytes": raw}]
        for key in _LEGACY_KEYS:
            st.session_state.pop(key, None)


def _clear_persisted_uploads() -> None:
    st.session_state[_SS_FILES] = []
    for key in _LEGACY_KEYS:
        st.session_state.pop(key, None)
    st.session_state[_SS_UPLOADER_NONCE] = int(
        st.session_state.get(_SS_UPLOADER_NONCE) or 0
    ) + 1


def _normalize_uploader_value(uploaded: Any) -> List[Any]:
    """Streamlit: un archivo → UploadedFile; varios → list."""
    if uploaded is None:
        return []
    if isinstance(uploaded, list):
        return [f for f in uploaded if f is not None]
    return [uploaded]


def _persist_uploaded_files(uploaded_list: Sequence[Any]) -> None:
    """
    Copia inmediata del lote del widget → session_state (merge por id).
    Si el widget vuelve vacío en el siguiente rerun (móvil), la lista se conserva.
    """
    if not uploaded_list:
        return

    by_id: Dict[str, Dict[str, Any]] = {
        str(item["id"]): dict(item)
        for item in (st.session_state.get(_SS_FILES) or [])
        if isinstance(item, dict) and item.get("id") and item.get("bytes")
    }

    for uploaded in uploaded_list:
        name = str(getattr(uploaded, "name", None) or "archivo")
        raw = bytes(uploaded.getvalue())
        size = getattr(uploaded, "size", None)
        file_id = f"{name}:{size if size is not None else len(raw)}"
        by_id[file_id] = {"id": file_id, "name": name, "bytes": raw}

    st.session_state[_SS_FILES] = list(by_id.values())


def _persisted_files() -> List[Dict[str, Any]]:
    items = st.session_state.get(_SS_FILES) or []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        data = item.get("bytes")
        name = item.get("name")
        if not data or not isinstance(data, (bytes, bytearray)):
            continue
        out.append(
            {
                "id": str(item.get("id") or f"{name}:{len(data)}"),
                "name": str(name or "archivo"),
                "bytes": bytes(data),
            }
        )
    return out


def _partition_files(
    files: Sequence[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    images: List[Dict[str, Any]] = []
    pdfs: List[Dict[str, Any]] = []
    other: List[Dict[str, Any]] = []
    for f in files:
        ext = _file_ext(str(f.get("name") or ""))
        if ext in _IMAGE_EXTS:
            images.append(f)
        elif ext in _PDF_EXTS:
            pdfs.append(f)
        else:
            other.append(f)
    return images, pdfs, other


def _mostrar_resultado_agregado(
    *,
    n_archivos: int,
    n_insertadas: int,
    n_duplicadas: int,
    n_temas: int,
    chunks_procesados: int,
    materias_tocadas: Dict[str, Any],
    warnings: List[str],
    raw_results: List[Any],
) -> None:
    detalle_mats = (
        ", ".join(f"{k}: {v}" for k, v in materias_tocadas.items())
        if materias_tocadas
        else "ninguna"
    )
    payload = {
        "archivos_procesados": n_archivos,
        "n_insertadas": n_insertadas,
        "n_duplicadas": n_duplicadas,
        "temas_upserted": n_temas,
        "chunks_procesados": chunks_procesados,
        "materias_tocadas": materias_tocadas,
        "warnings": warnings,
        "resultados": [
            r.to_dict() if hasattr(r, "to_dict") else r for r in raw_results
        ],
    }
    st.session_state["student_ingesta_last"] = payload
    st.success(
        f"Se agregaron **{n_insertadas}** preguntas privadas · "
        f"archivos={n_archivos} · fragmentos={chunks_procesados} · "
        f"duplicadas={n_duplicadas} · temas={n_temas}. "
        f"Distribución por materia: {detalle_mats}. "
        "Practica con fuente «Solo Mis Guías» o «Todo»."
    )
    if warnings:
        with st.expander("Avisos del proceso (delta / densidad / clasificación)"):
            for w in warnings:
                st.write(f"- {w}")


def render_student_ingestion_tab(*, usuario_id: int) -> None:
    st.subheader("📚 Generador de Simulacros con tu Guía")
    st.caption(
        "**1.** Sube uno o varios archivos: **PDF** y/o **fotos** (JPG/PNG) del compendio.  "
        "**2.** La IA extrae preguntas A–E privadas, las clasifica en la materia "
        "oficial correcta y omite temas que ya tienes."
    )

    _ensure_ingesta_session_defaults()
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

    uploader_key = f"student_ingesta_upload_{int(st.session_state[_SS_UPLOADER_NONCE])}"
    uploaded = st.file_uploader(
        "PDF(s) o foto(s) de tu guía / compendio",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key=uploader_key,
        help=(
            "Puedes seleccionar varias fotos a la vez (p. ej. 3 páginas del compendio). "
            "En el celular se guardan en la sesión apenas las eliges."
        ),
    )

    uploaded_list = _normalize_uploader_value(uploaded)
    if uploaded_list:
        try:
            _persist_uploaded_files(uploaded_list)
        except Exception as exc:
            st.error(f"No se pudo guardar el lote en sesión: {exc}")

    files = _persisted_files()
    has_files = len(files) > 0
    images, pdfs, other = _partition_files(files)

    max_pages: Optional[int] = None

    if has_files:
        total_bytes = sum(len(f["bytes"]) for f in files)
        st.success(
            f"**{len(files)} archivo(s) listos en sesión** "
            f"({total_bytes:,} bytes · "
            f"{len(images)} foto(s), {len(pdfs)} PDF(s)"
            + (f", {len(other)} otro(s)" if other else "")
            + ")."
        )
        with st.expander("Ver archivos en sesión", expanded=len(files) <= 5):
            for f in files:
                st.caption(f"• `{f['name']}` ({len(f['bytes']):,} bytes)")

        clear_col, _ = st.columns([1, 2])
        with clear_col:
            if st.button(
                "Limpiar archivos",
                use_container_width=True,
                key="student_ingesta_clear_files",
            ):
                _clear_persisted_uploads()
                st.rerun()

    if pdfs:
        longest = max((_count_pdf_pages(p["bytes"]) for p in pdfs), default=0)
        if longest > PAGINAS_LARGAS:
            st.warning(
                f"Al menos un PDF tiene **{longest} páginas**. "
                "El límite aplica a cada PDF del lote."
            )
            max_pages = st.slider(
                "Procesar primeras N páginas (por PDF)",
                min_value=5,
                max_value=min(MAX_PAGINAS_HARD, max(longest, 5)),
                value=min(MAX_PAGINAS_DEFAULT, longest),
                key="student_ingesta_max_pages",
            )
        elif longest > 0:
            st.caption(f"PDF(s): hasta **{longest}** páginas · se analizarán completos.")

    if images:
        st.caption(
            "Modo foto (lote): la IA procesará cada imagen en secuencia "
            "(más estable que un único payload enorme)."
        )
        preview_n = min(6, len(images))
        cols = st.columns(min(3, preview_n))
        for i in range(preview_n):
            with cols[i % len(cols)]:
                try:
                    st.image(images[i]["bytes"], caption=images[i]["name"], width=180)
                except Exception:
                    st.caption(images[i]["name"])
        if len(images) > preview_n:
            st.caption(f"… y {len(images) - preview_n} foto(s) más.")

    if other:
        st.warning(
            "Hay archivos con formato no soportado: "
            + ", ".join(f"`{o['name']}`" for o in other)
            + ". Usa PDF, PNG, JPG o JPEG, o limpia el lote."
        )

    if st.button(
        "Analizar mi guía con IA",
        type="primary",
        disabled=not has_files or (not images and not pdfs),
        use_container_width=True,
        key="student_ingesta_run",
    ):
        files = _persisted_files()
        images, pdfs, other = _partition_files(files)
        if not images and not pdfs:
            st.error("Sube al menos un PDF o una foto válida primero.")
            return

        progress = st.progress(0, text="Preparando tu lote…")
        status = st.empty()
        results: List[BancoIngestionResult] = []
        warnings: List[str] = []
        n_insertadas = 0
        n_duplicadas = 0
        n_temas = 0
        chunks_procesados = 0
        materias_tocadas: Dict[str, Any] = {}
        n_archivos = len(images) + len(pdfs)

        def _absorb(result: BancoIngestionResult) -> None:
            nonlocal n_insertadas, n_duplicadas, n_temas, chunks_procesados
            results.append(result)
            pers = result.persistencia or {}
            n_insertadas += int(pers.get("n_insertadas") or 0)
            n_duplicadas += int(pers.get("n_duplicadas") or 0)
            n_temas += int(pers.get("temas_upserted") or 0)
            chunks_procesados += int(result.chunks_procesados or 0)
            for k, v in (pers.get("materias_tocadas") or {}).items():
                materias_tocadas[k] = int(materias_tocadas.get(k) or 0) + int(v or 0)
            warnings.extend(list(result.warnings or []))

        try:
            steps_done = 0
            total_steps = (1 if images else 0) + len(pdfs)

            # 1) Todas las fotos → pipeline secuencial (Base64 + OpenRouter por imagen)
            if images:
                status.info(
                    f"Analizando {len(images)} foto(s) con IA multimodal (secuencial)…"
                )
                progress.progress(
                    min(0.95, 0.05 + 0.9 * (steps_done / max(total_steps, 1))),
                    text=f"Fotos 0/{len(images)}…",
                )

                def _on_img(idx: int, total: int, msg: str) -> None:
                    local = (idx + 1) / max(total, 1) if total else 1.0
                    if idx >= total:
                        local = 1.0
                    overall = (steps_done + local) / max(total_steps, 1)
                    progress.progress(min(0.95, 0.05 + 0.9 * overall), text=msg)
                    status.write(msg)

                lote_nombre = (
                    images[0]["name"]
                    if len(images) == 1
                    else f"lote_{len(images)}_fotos"
                )
                st.info(
                    f"Lote de fotos · {len(images)} imagen(es) · "
                    "cada una se convierte a Base64 y se envía a OpenRouter."
                )
                img_result = extract_banco_preguntas_from_images(
                    [{"bytes": img["bytes"], "nombre": img["name"]} for img in images],
                    materia_id=None,
                    auto_clasificar=True,
                    origen_contenido="imagen",
                    fuente="openrouter",
                    nombre_archivo_fuente=str(lote_nombre),
                    propietario_usuario_id=uid,
                    persist=True,
                    pause_between_images_s=0.4,
                    on_image_progress=_on_img,
                )
                _absorb(img_result)
                steps_done += 1

            # 2) Cada PDF → chunks de texto (misma lógica de siempre)
            for pdf_i, pdf in enumerate(pdfs):
                status.info(
                    f"Extrayendo PDF {pdf_i + 1}/{len(pdfs)}: `{pdf['name']}`…"
                )
                progress.progress(
                    min(0.95, 0.05 + 0.9 * (steps_done / max(total_steps, 1))),
                    text=f"PDF {pdf_i + 1}/{len(pdfs)}…",
                )
                doc = process_pdf(
                    pdf["bytes"],
                    allow_ocr=True,
                    source_filename=pdf["name"],
                    max_pages=max_pages,
                )
                if not doc.text or not doc.chunks:
                    warnings.append(
                        f"PDF «{pdf['name']}»: sin texto útil (¿escaneado sin OCR?)."
                    )
                    for w in doc.warnings:
                        warnings.append(f"PDF «{pdf['name']}»: {w}")
                    steps_done += 1
                    continue

                nombre_pdf = (
                    (doc.meta or {}).get("nombre_archivo_fuente") or pdf["name"]
                )
                pages_proc = (doc.meta or {}).get("pages_procesadas") or doc.page_count
                st.info(
                    f"PDF listo · `{nombre_pdf}` · "
                    f"{pages_proc}/{doc.page_count} págs · "
                    f"**{len(doc.chunks)} fragmentos** · método `{doc.method}`"
                )

                def _on_chunk(idx: int, total: int, msg: str, _sd=steps_done) -> None:
                    local = (idx + 1) / max(total, 1) if total else 1.0
                    if idx >= total:
                        local = 1.0
                    overall = (_sd + local) / max(total_steps, 1)
                    progress.progress(min(0.95, 0.05 + 0.9 * overall), text=msg)
                    status.write(msg)

                pdf_result = extract_banco_preguntas_from_chunks(
                    doc.chunks,
                    materia_id=None,
                    auto_clasificar=True,
                    origen_contenido="pdf",
                    fuente="openrouter",
                    nombre_archivo_fuente=str(nombre_pdf),
                    propietario_usuario_id=uid,
                    persist=True,
                    pause_between_chunks_s=0.4,
                    on_chunk_progress=_on_chunk,
                )
                _absorb(pdf_result)
                steps_done += 1

            if not results and warnings:
                progress.empty()
                status.empty()
                st.error("No se pudo extraer contenido útil del lote.")
                with st.expander("Detalle"):
                    for w in warnings:
                        st.write(f"- {w}")
                return

            progress.progress(100, text="¡Listo! Preguntas guardadas en tu bóveda privada.")
            status.success("Análisis del lote completado.")
            _mostrar_resultado_agregado(
                n_archivos=n_archivos,
                n_insertadas=n_insertadas,
                n_duplicadas=n_duplicadas,
                n_temas=n_temas,
                chunks_procesados=chunks_procesados,
                materias_tocadas=materias_tocadas,
                warnings=warnings,
                raw_results=results,
            )
        except Exception as exc:
            st.error(f"No se pudo procesar tu guía: {exc}")

    last: Optional[Dict[str, Any]] = st.session_state.get("student_ingesta_last")
    if last:
        with st.expander("Última carga (detalle)"):
            st.json(last)
