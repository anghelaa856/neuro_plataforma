"""
Ingesta self-service del estudiante: «Generador de Simulacros con tu Guía».

Límite duro: máximo 3 archivos por lote (Space gratuito / anti-OOM).
Carga perezosa: se hace .read()/.getvalue() de UNA imagen a la vez → comprimir
→ analizar → liberar RAM → siguiente.
"""

from __future__ import annotations

import gc
from typing import Any, Dict, List, Optional, Sequence, Tuple

import streamlit as st
from pypdf import PdfReader
from io import BytesIO

from app.infrastructure.database.banco_repository import banco_repository
from app.services.banco_extraction_service import (
    BancoIngestionResult,
    extract_banco_preguntas_from_chunks,
    extract_banco_preguntas_from_images,
)
from app.services.content_service import compress_image_bytes
from app.services.pdf_processor import process_pdf

# Umbral a partir del cual pedimos acotar páginas
PAGINAS_LARGAS = 20
DEFAULT_PAGE_START = 1
DEFAULT_PAGE_END = 10
MAX_PAGINA_INPUT = 5000
MAX_RANGE_WARN = 80  # aviso suave anti-OOM (no bloquea)

# Anti-OOM en Hugging Face Spaces (CPU básico)
MAX_FILES_PER_BATCH = 3

_IMAGE_EXTS = {"png", "jpg", "jpeg"}
_PDF_EXTS = {"pdf"}

# Metadatos / cola perezosa (NO guardar N originales full-res de golpe)
_SS_QUEUE = "student_ingesta_upload_queue"  # List[{id, name, kind}]
_SS_COMPRESSED = "student_ingesta_compressed_cache"  # id → bytes ya comprimidos
_SS_UPLOADER_NONCE = "student_ingesta_uploader_nonce"
_LEGACY_KEYS = (
    "student_ingesta_uploaded_files",
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
    if _SS_QUEUE not in st.session_state:
        st.session_state[_SS_QUEUE] = []
    if _SS_COMPRESSED not in st.session_state:
        st.session_state[_SS_COMPRESSED] = {}
    # Limpiar legado que acumulaba bytes full-res
    for key in _LEGACY_KEYS:
        st.session_state.pop(key, None)


def _clear_persisted_uploads() -> None:
    st.session_state[_SS_QUEUE] = []
    st.session_state[_SS_COMPRESSED] = {}
    for key in _LEGACY_KEYS:
        st.session_state.pop(key, None)
    st.session_state[_SS_UPLOADER_NONCE] = int(
        st.session_state.get(_SS_UPLOADER_NONCE) or 0
    ) + 1
    gc.collect()


def _normalize_uploader_value(uploaded: Any) -> List[Any]:
    if uploaded is None:
        return []
    if isinstance(uploaded, list):
        return [f for f in uploaded if f is not None]
    return [uploaded]


def _read_uploaded_bytes_once(uploaded: Any) -> bytes:
    """Lee el búfer del UploadedFile una sola vez (lazy)."""
    if hasattr(uploaded, "read"):
        try:
            uploaded.seek(0)
        except Exception:
            pass
        data = uploaded.read()
        if data:
            return bytes(data)
    return bytes(uploaded.getvalue())


def _register_queue_metadata(uploaded_list: Sequence[Any]) -> None:
    """Solo nombres/ids en sesión — sin .read() aún."""
    queue: List[Dict[str, str]] = []
    for uploaded in uploaded_list:
        name = str(getattr(uploaded, "name", None) or "archivo")
        size = getattr(uploaded, "size", None)
        file_id = f"{name}:{size if size is not None else 'pending'}"
        ext = _file_ext(name)
        if ext in _IMAGE_EXTS:
            kind = "image"
        elif ext in _PDF_EXTS:
            kind = "pdf"
        else:
            kind = "other"
        queue.append({"id": file_id, "name": name, "kind": kind})
    st.session_state[_SS_QUEUE] = queue


def _queue_items() -> List[Dict[str, str]]:
    items = st.session_state.get(_SS_QUEUE) or []
    return [dict(x) for x in items if isinstance(x, dict) and x.get("name")]


def _lazy_load_and_compress_one(
    uploaded: Any,
    *,
    name: str,
    kind: str,
) -> Tuple[str, bytes]:
    """
    .read() de UN archivo → comprimir si es imagen → devolver bytes livianos.
    No deja el original en variables al salir.
    """
    raw = _read_uploaded_bytes_once(uploaded)
    out_name = name
    try:
        if kind == "image":
            compressed = compress_image_bytes(raw, max_side=1024, jpeg_quality=70)
            del raw
            raw = None  # type: ignore
            gc.collect()
            if not out_name.lower().endswith((".jpg", ".jpeg")):
                base = out_name.rsplit(".", 1)[0] if "." in out_name else out_name
                out_name = f"{base}.jpg"
            return out_name, compressed
        return out_name, raw
    except Exception:
        # Si falla compress, devolver original (último recurso)
        return out_name, raw if isinstance(raw, (bytes, bytearray)) else b""


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
        "**1.** Sube **máximo 3 fotos** (o PDFs) del compendio por lote.  "
        "**2.** La IA extrae preguntas A–E, las clasifica por materia y omite temas ya cubiertos."
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

    st.info(
        f"Límite del servidor gratuito: **máximo {MAX_FILES_PER_BATCH} archivos** "
        "por carga (≤ 30 MB en total). Sube de a 3 fotos y vuelve a cargar si necesitas más."
    )

    uploader_key = f"student_ingesta_upload_{int(st.session_state[_SS_UPLOADER_NONCE])}"
    uploaded = st.file_uploader(
        "PDF(s) o foto(s) — máximo 3 a la vez",
        type=["pdf", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key=uploader_key,
        help=(
            "Sube máximo 3 fotos a la vez. "
            "Cada imagen se lee y comprime de a una para no saturar la memoria."
        ),
    )

    uploaded_list = _normalize_uploader_value(uploaded)

    # Validación dura ANTES de cualquier .read() / getvalue()
    if len(uploaded_list) > MAX_FILES_PER_BATCH:
        st.error(
            "⚠️ Límite de servidor gratuito: Por favor, sube un máximo de 3 fotos "
            "a la vez para evitar sobrecargar la memoria."
        )
        st.stop()

    if uploaded_list:
        _register_queue_metadata(uploaded_list)

    queue = _queue_items()
    # Preferir widget vivo; si móvil lo vació, usar cola de nombres (sin bytes)
    live_files = uploaded_list
    has_selection = bool(live_files) or bool(queue)
    n_selected = len(live_files) if live_files else len(queue)

    page_start: int = DEFAULT_PAGE_START
    page_end: int = DEFAULT_PAGE_END
    range_ok = True

    def _kind_of_name(name: str) -> str:
        ext = _file_ext(name)
        if ext in _IMAGE_EXTS:
            return "image"
        if ext in _PDF_EXTS:
            return "pdf"
        return "other"

    display_items: List[Dict[str, str]] = []
    if live_files:
        for uf in live_files:
            nm = str(getattr(uf, "name", None) or "archivo")
            display_items.append({"name": nm, "kind": _kind_of_name(nm)})
    else:
        display_items = [
            {
                "name": str(q.get("name") or "archivo"),
                "kind": str(q.get("kind") or "other"),
            }
            for q in queue
        ]

    n_images = sum(1 for d in display_items if d["kind"] == "image")
    n_pdfs = sum(1 for d in display_items if d["kind"] == "pdf")
    n_other = sum(1 for d in display_items if d["kind"] == "other")

    if has_selection:
        st.success(
            f"**{n_selected} archivo(s) listos en sesión** · "
            f"{n_images} foto(s), {n_pdfs} PDF(s)"
            + (f", {n_other} otro(s)" if n_other else "")
            + ". Aún no se leyeron a memoria: el análisis carga de a una."
        )
        with st.expander("Ver archivos seleccionados", expanded=True):
            for d in display_items:
                st.caption(f"• `{d['name']}` ({d['kind']})")

        clear_col, _ = st.columns([1, 2])
        with clear_col:
            if st.button(
                "Limpiar archivos",
                use_container_width=True,
                key="student_ingesta_clear_files",
            ):
                _clear_persisted_uploads()
                st.rerun()

    if n_pdfs:
        st.caption(
            "Para PDFs largos, elegí el **rango de páginas** del capítulo "
            "(ej. 115–130). Se aplica a cada PDF del lote."
        )
        c_ini, c_fin = st.columns(2)
        with c_ini:
            page_start = int(
                st.number_input(
                    "Página de inicio",
                    min_value=1,
                    max_value=MAX_PAGINA_INPUT,
                    value=DEFAULT_PAGE_START,
                    step=1,
                    key="student_ingesta_page_start",
                )
            )
        with c_fin:
            page_end = int(
                st.number_input(
                    "Página final",
                    min_value=1,
                    max_value=MAX_PAGINA_INPUT,
                    value=DEFAULT_PAGE_END,
                    step=1,
                    key="student_ingesta_page_end",
                )
            )
        if page_end < page_start:
            range_ok = False
            st.error(
                "La página final no puede ser menor que la de inicio. "
                f"Ajustá el rango (ahora: {page_start} → {page_end})."
            )
        else:
            n_range = page_end - page_start + 1
            st.caption(f"Se leerán las páginas **{page_start}–{page_end}** ({n_range} pág.).")
            if n_range > MAX_RANGE_WARN:
                st.warning(
                    f"El rango tiene {n_range} páginas (>{MAX_RANGE_WARN}). "
                    "En el plan gratuito conviene trocear el capítulo para evitar OOM."
                )

    if n_images:
        st.caption(
            "Sube máximo 3 fotos a la vez. Cada una se comprime (≤1024px) y se analiza "
            "sola antes de pasar a la siguiente."
        )

    if n_other:
        st.warning("Hay archivos con formato no soportado. Usa PDF, PNG, JPG o JPEG.")

    can_run = (
        has_selection
        and (n_images + n_pdfs) > 0
        and bool(live_files)
        and range_ok
    )
    if has_selection and not live_files:
        st.warning(
            "La selección del celular se perdió en el rerun y no hay bytes en memoria "
            "(modo anti-OOM). Volvé a elegir las mismas fotos (máx. 3) y pulsá "
            "**Analizar** enseguida."
        )

    if st.button(
        "Analizar mi guía con IA",
        type="primary",
        disabled=not can_run,
        use_container_width=True,
        key="student_ingesta_run",
    ):
        if not live_files:
            st.error("No hay archivos vivos en el uploader. Subí de nuevo (máx. 3).")
            return
        if len(live_files) > MAX_FILES_PER_BATCH:
            st.error(
                "⚠️ Límite de servidor gratuito: Por favor, sube un máximo de 3 fotos "
                "a la vez para evitar sobrecargar la memoria."
            )
            return

        progress = st.progress(0, text="Preparando lote perezoso…")
        status = st.empty()
        results: List[BancoIngestionResult] = []
        warnings: List[str] = []
        n_insertadas = 0
        n_duplicadas = 0
        n_temas = 0
        chunks_procesados = 0
        materias_tocadas: Dict[str, Any] = {}
        n_archivos = 0

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

        total = len(live_files)
        try:
            for idx, uploaded_file in enumerate(live_files):
                name = str(getattr(uploaded_file, "name", None) or f"archivo_{idx + 1}")
                kind = _kind_of_name(name)
                status.info(f"Leyendo archivo {idx + 1}/{total}: `{name}`…")
                progress.progress(
                    min(0.95, (idx) / max(total, 1)),
                    text=f"{idx + 1}/{total} · cargando…",
                )

                if kind == "other":
                    warnings.append(f"Omitido formato no soportado: {name}")
                    continue

                # --- Lazy: read → compress → process → free ---
                out_name, payload = _lazy_load_and_compress_one(
                    uploaded_file, name=name, kind=kind
                )
                n_archivos += 1

                if kind == "image":
                    status.write(
                        f"Analizando foto {idx + 1}/{total} con IA (comprimida)…"
                    )

                    def _on_img(i: int, t: int, msg: str) -> None:
                        status.write(msg)

                    img_result = extract_banco_preguntas_from_images(
                        [{"bytes": payload, "nombre": out_name}],
                        materia_id=None,
                        auto_clasificar=True,
                        origen_contenido="imagen",
                        fuente="openrouter",
                        nombre_archivo_fuente=str(out_name),
                        propietario_usuario_id=uid,
                        persist=True,
                        pause_between_images_s=0.0,
                        on_image_progress=_on_img,
                    )
                    _absorb(img_result)
                else:
                    status.write(f"Extrayendo PDF {idx + 1}/{total}: `{out_name}`…")
                    doc = process_pdf(
                        payload,
                        allow_ocr=True,
                        source_filename=out_name,
                        page_start=int(page_start),
                        page_end=int(page_end),
                    )
                    if not doc.text or not doc.chunks:
                        warnings.append(
                            f"PDF «{out_name}»: sin texto útil (¿escaneado sin OCR?)."
                        )
                        for w in doc.warnings:
                            warnings.append(f"PDF «{out_name}»: {w}")
                    else:
                        nombre_pdf = (
                            (doc.meta or {}).get("nombre_archivo_fuente") or out_name
                        )
                        p0 = (doc.meta or {}).get("page_start")
                        p1 = (doc.meta or {}).get("page_end")
                        rango = (
                            f"págs {p0}–{p1}"
                            if p0 and p1
                            else f"{doc.meta.get('pages_procesadas', '?')} págs"
                        )
                        st.info(
                            f"PDF listo · `{nombre_pdf}` · {rango} · "
                            f"**{len(doc.chunks)} fragmentos** · método `{doc.method}`"
                        )

                        def _on_chunk(i: int, t: int, msg: str) -> None:
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
                            pause_between_chunks_s=0.35,
                            on_chunk_progress=_on_chunk,
                        )
                        _absorb(pdf_result)
                    del doc

                del payload
                gc.collect()
                progress.progress(
                    min(0.95, (idx + 1) / max(total, 1)),
                    text=f"{idx + 1}/{total} · listo",
                )

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
            # Liberar cola tras éxito
            _clear_persisted_uploads()
        except Exception as exc:
            st.error(f"No se pudo procesar tu guía: {exc}")
            gc.collect()

    last: Optional[Dict[str, Any]] = st.session_state.get("student_ingesta_last")
    if last:
        with st.expander("Última carga (detalle)"):
            st.json(last)
