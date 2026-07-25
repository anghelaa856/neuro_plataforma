"""
Aduana de Limpieza PDF → texto listo para OpenRouter / TutorEngine.

Pipeline:
  1) Extracción nativa (pypdf, opcionalmente PyMuPDF)
  2) Si densidad de texto baja → fallback OCR (pdf2image + pytesseract)
  3) Sanitización / "costura" de palabras rotas y saltos de línea
  4) Fragmentación con solapamiento (~15%) para ventanas LLM

OCR y PyMuPDF son opcionales: el módulo degrada con claridad si faltan
binarios del sistema (poppler, tesseract) — típico en Streamlit Cloud.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Umbral: por debajo de N caracteres alfanuméricos / página → PDF probablemente escaneado.
DEFAULT_MIN_CHARS_PER_PAGE = 80
# Ventana LLM (aprox. tokens). Español ≈ 4 caracteres/token.
DEFAULT_CHUNK_TOKENS = 1800
DEFAULT_OVERLAP_RATIO = 0.15
CHARS_PER_TOKEN = 4.0


@dataclass
class ProcessedDocument:
    """Resultado de la aduana de limpieza."""

    text: str
    chunks: List[str] = field(default_factory=list)
    method: str = "native"  # native | native+pymupdf | ocr | hybrid | empty
    page_count: int = 0
    chars_per_page: float = 0.0
    used_ocr: bool = False
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 1) Extracción híbrida
# ---------------------------------------------------------------------------

def _count_pages_pypdf(file_bytes: bytes) -> int:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(file_bytes))
    return len(reader.pages)


def extract_native_pypdf(file_bytes: bytes) -> Tuple[str, int]:
    """Extracción de texto embebido con pypdf (siempre disponible en el stack)."""
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(file_bytes))
    parts: List[str] = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n\n".join(parts), len(reader.pages)


def extract_native_pymupdf(file_bytes: bytes) -> Tuple[str, int]:
    """
    Extracción nativa con PyMuPDF (mejor en PDFs 'sucios' con layout raro).
    Requiere: pip install pymupdf
    """
    import fitz  # type: ignore

    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        parts = [page.get_text("text") or "" for page in doc]
        return "\n\n".join(parts), doc.page_count
    finally:
        doc.close()


def _alphanumeric_density(text: str, page_count: int) -> float:
    if page_count <= 0:
        return 0.0
    alnum = sum(1 for ch in text if ch.isalnum())
    return alnum / float(page_count)


def needs_ocr(
    text: str,
    page_count: int,
    *,
    min_chars_per_page: float = DEFAULT_MIN_CHARS_PER_PAGE,
) -> bool:
    """True si el PDF parece escaneado / casi sin capa de texto."""
    if page_count <= 0:
        return True
    density = _alphanumeric_density(text, page_count)
    # También: mucho texto pero casi solo basura de encoding
    stripped = (text or "").strip()
    if not stripped:
        return True
    return density < min_chars_per_page


def extract_ocr_tesseract(
    file_bytes: bytes,
    *,
    lang: str = "spa+eng",
    dpi: int = 200,
    max_pages: Optional[int] = None,
) -> Tuple[str, int]:
    """
    Fallback OCR: rasteriza páginas (pdf2image/poppler) + pytesseract.

    Dependencias de sistema:
      - poppler (pdftoppm)
      - tesseract-ocr + datos spa/eng
    """
    from pdf2image import convert_from_bytes  # type: ignore
    import pytesseract  # type: ignore

    images = convert_from_bytes(file_bytes, dpi=dpi)
    if max_pages is not None:
        images = images[: max(1, int(max_pages))]

    parts: List[str] = []
    for idx, image in enumerate(images, start=1):
        try:
            page_text = pytesseract.image_to_string(image, lang=lang) or ""
        except pytesseract.TesseractError:
            # Si faltan packs de idioma, reintenta solo eng
            page_text = pytesseract.image_to_string(image, lang="eng") or ""
        parts.append(page_text)
        logger.debug("OCR página %s/%s → %s chars", idx, len(images), len(page_text))

    return "\n\n".join(parts), len(images)


def extract_pdf_text_hybrid(
    file_bytes: bytes,
    *,
    min_chars_per_page: float = DEFAULT_MIN_CHARS_PER_PAGE,
    allow_ocr: bool = True,
    ocr_lang: str = "spa+eng",
    ocr_max_pages: Optional[int] = None,
) -> Tuple[str, str, int, List[str]]:
    """
    Intenta nativo → (opcional) PyMuPDF → OCR.

    Returns:
        (texto_crudo, method, page_count, warnings)
    """
    warnings: List[str] = []
    text, pages = extract_native_pypdf(file_bytes)
    method = "native"

    # Segunda pasada nativa si pypdf extrajo poco y hay PyMuPDF
    if needs_ocr(text, pages, min_chars_per_page=min_chars_per_page):
        try:
            text_m, pages_m = extract_native_pymupdf(file_bytes)
            if _alphanumeric_density(text_m, pages_m) > _alphanumeric_density(text, pages):
                text, pages = text_m, pages_m
                method = "native+pymupdf"
                warnings.append("pypdf con baja densidad; se usó PyMuPDF.")
        except ImportError:
            warnings.append("PyMuPDF no instalado; se omite segunda pasada nativa.")
        except Exception as exc:
            warnings.append(f"PyMuPDF falló: {exc}")

    if allow_ocr and needs_ocr(text, pages, min_chars_per_page=min_chars_per_page):
        try:
            text_ocr, pages_ocr = extract_ocr_tesseract(
                file_bytes,
                lang=ocr_lang,
                max_pages=ocr_max_pages,
            )
            if _alphanumeric_density(text_ocr, pages_ocr) > _alphanumeric_density(text, max(pages, 1)):
                text, pages = text_ocr, pages_ocr
                method = "ocr" if method.startswith("native") and not text.strip() else "hybrid"
                warnings.append(
                    "Densidad nativa baja: activado fallback OCR (pdf2image+tesseract)."
                )
            else:
                warnings.append("OCR no mejoró la densidad; se conserva texto nativo.")
        except ImportError:
            warnings.append(
                "OCR no disponible (instale pdf2image, pytesseract, poppler y tesseract). "
                "El PDF parece escaneado: la calidad del LLM puede degradarse."
            )
            method = method  # noqa: PLW0127 — se mantiene nativo pobre
        except Exception as exc:
            warnings.append(f"OCR falló: {exc}")

    if not (text or "").strip():
        method = "empty"
        warnings.append("No se extrajo texto usable del PDF.")

    return text, method, pages, warnings


# ---------------------------------------------------------------------------
# 2) Sanitización / Text Healing
# ---------------------------------------------------------------------------

# Encabezados / pies típicos de academias y material UNA
_ACADEMIA_HEADER_PATTERNS = [
    r"^\s*academia\s+[\wáéíóúñ.\- ]{2,40}\s*$",
    r"^\s*ciclo\s+(ordinario|verano|intensivo|anual)\b.*$",
    r"^\s*área\s+de\s+biom[eé]dicas\s*$",
    r"^\s*examen\s+de\s+admisi[oó]n\b.*$",
    r"^\s*universidad\s+nacional\s+del?\s+altiplan[oó]\b.*$",
    r"^\s*una[\-\s]?puno\b.*$",
    r"^\s*prospecto\s+\d{4}\b.*$",
    r"^\s*copyright\b.*$",
    r"^\s*todos\s+los\s+derechos\s+reservados\b.*$",
    r"^\s*www\.[^\s]+$",
    r"^\s*https?://\S+\s*$",
    r"^\s*página\s+\d+\s*(de\s+\d+)?\s*$",
    r"^\s*pag\.?\s*\d+\s*$",
    r"^\s*-\s*\d+\s*-\s*$",
    r"^\s*\d+\s*/\s*\d+\s*$",
]

_COMPILED_HEADERS = [re.compile(p, re.IGNORECASE) for p in _ACADEMIA_HEADER_PATTERNS]


def heal_text(raw_text: str) -> str:
    """
    Costura y sanitiza texto entrecortado de PDFs/OCR antes del LLM.

    Orden deliberado:
      A) normalizar finales de línea y espacios raros
      B) unir guiones de separación silábica (aún con \\n presentes)
      C) filtrar ruido de academia / páginas / URLs (línea a línea)
      D) coser saltos simples que rompen oraciones (conservar \\n\\n)
      E) compactar espacios y párrafos
    """
    if not raw_text:
        return ""

    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    # Form-feed y caracteres de control no imprimibles (salvo \n \t)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)

    # --- B) Guiones de silabación al final de línea ---
    # Caso típico de PDF: "respirato-\nrio" → "respiratorio"
    #
    # ([A-Za-zÁÉÍÓÚÜÑáéíóúüñ])  → última letra de la palabra partida
    # -                           → guion tipográfico de corte silábico
    # \s*\n\s*                    → salto (+ espacios basura del OCR)
    # ([a-záéíóúüñ])              → continuación en minúscula
    #                              (evita unir "Fin-\nCapítulo")
    # Sustitución r"\1\2" elimina el guion y el salto, dejando la palabra
    # completa sin espacio intermedio.
    text = re.sub(
        r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ])-\s*\n\s*([a-záéíóúüñ])",
        r"\1\2",
        text,
    )
    # Soft hyphen (U+00AD) y guiones tipográficos que el OCR inserta igual:
    text = re.sub(
        r"([A-Za-zÁÉÍÓÚÜÑáéíóúüñ])[\u00ad‑–—]\s*\n\s*([a-záéíóúüñ])",
        r"\1\2",
        text,
    )

    # --- C) Filtrado línea a línea de ruido (ANTES de coser oraciones) ---
    kept: List[str] = []
    for line in text.split("\n"):
        raw = line.strip()
        if not raw:
            kept.append("")  # preserva separación de párrafo
            continue

        # Números de página huérfanos: "12", "— 12 —", "12."
        if re.fullmatch(r"[\W_]*\d{1,4}[\W_]*", raw):
            continue

        # URLs / emails sueltos
        if re.fullmatch(r"https?://\S+", raw, flags=re.I):
            continue
        if re.fullmatch(r"www\.\S+", raw, flags=re.I):
            continue
        if re.fullmatch(r"[\w.\-]+@[\w.\-]+\.\w+", raw):
            continue

        if any(p.search(raw) for p in _COMPILED_HEADERS):
            continue

        # Líneas solo de puntos/guiones decorativos
        if re.fullmatch(r"[\.·•\-_=]{3,}", raw):
            continue

        kept.append(raw)

    text = "\n".join(kept)

    # --- D) Costura de oraciones rotas por \n simple ---
    # Conservamos párrafos reales (\n\n). Un salto SIMPLE entre tokens
    # no vacíos se reemplaza por espacio:
    #
    #   (?<=\S)   → hay carácter no-espacio antes del salto
    #   \n        → el salto forzado del PDF
    #   (?!\n)    → el siguiente NO es otro \n  ⇒ no es fin de párrafo
    #   (?=\S)    → hay contenido después  ⇒ no es línea residual vacía
    #
    # Ej.: "La hemoglobina\ntransporta O2." → "La hemoglobina transporta O2."
    #      "párrafo A.\n\nPárrafo B."       → se mantiene el \n\n
    text = re.sub(r"(?<=\S)\n(?!\n)(?=\S)", " ", text)

    # Espacio faltante tras puntuación (artefacto OCR): "oxígeno.La" → "oxígeno. La"
    text = re.sub(r"([a-záéíóúüñ])([.!?])([A-ZÁÉÍÓÚÜÑ])", r"\1\2 \3", text)

    # --- E) Compactación ---
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 3) Fragmentación con solapamiento
# ---------------------------------------------------------------------------

_SEPARATORS: Sequence[str] = (
    "\n\n",  # párrafos
    "\n",  # líneas
    ". ",  # oraciones
    "; ",
    ", ",
    " ",  # palabras
    "",  # último recurso: corte duro
)


def _estimate_tokens(text: str) -> int:
    return max(1, int(round(len(text) / CHARS_PER_TOKEN)))


def _split_once(text: str, separator: str) -> List[str]:
    if separator == "":
        return list(text)
    if not separator:
        return [text]
    parts = text.split(separator)
    # Reinyecta el separador en todos menos el último para no perder puntuación
    out: List[str] = []
    for i, part in enumerate(parts):
        if i < len(parts) - 1:
            out.append(part + separator)
        else:
            if part:
                out.append(part)
    return out


def chunk_text(
    text: str,
    *,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> List[str]:
    """
    Divisor estilo RecursiveCharacterTextSplitter (nativo, sin LangChain).

    - Tamaño objetivo: ``chunk_tokens`` (≈ 1500–2000).
    - Overlap ≥ 15% entre fragmentos consecutivos para no cortar conceptos.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    max_chars = max(200, int(chunk_tokens * CHARS_PER_TOKEN))
    overlap_chars = max(50, int(max_chars * max(0.15, float(overlap_ratio))))

    if len(cleaned) <= max_chars:
        return [cleaned]

    # Partición recursiva por separadores semánticos
    pieces = _recursive_split(cleaned, max_chars)
    if not pieces:
        return [cleaned]

    # Ensambla con ventana deslizante + overlap
    chunks: List[str] = []
    buffer = ""
    for piece in pieces:
        candidate = (buffer + piece) if buffer else piece
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer.strip():
            chunks.append(buffer.strip())
            # Solapamiento: conserva la cola del buffer
            tail = buffer[-overlap_chars:] if len(buffer) > overlap_chars else buffer
            buffer = tail + piece
            # Si aún excede (pieza enorme), fuerza corte
            while len(buffer) > max_chars:
                chunks.append(buffer[:max_chars].strip())
                buffer = buffer[max_chars - overlap_chars :]
        else:
            buffer = piece

    if buffer.strip():
        chunks.append(buffer.strip())

    # Dedup trivial de chunks idénticos consecutivos
    deduped: List[str] = []
    for ch in chunks:
        if not deduped or deduped[-1] != ch:
            deduped.append(ch)
    return deduped


def _recursive_split(text: str, max_chars: int) -> List[str]:
    if len(text) <= max_chars:
        return [text]

    for sep in _SEPARATORS:
        parts = _split_once(text, sep)
        if len(parts) == 1:
            continue
        good: List[str] = []
        ok = True
        for part in parts:
            if len(part) <= max_chars:
                if part:
                    good.append(part)
            else:
                # Recurse en la pieza grande
                sub = _recursive_split(part, max_chars)
                if not sub:
                    ok = False
                    break
                good.extend(sub)
        if ok and good:
            return good
    # Fallback: cortes fijos
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


# ---------------------------------------------------------------------------
# Pipeline público
# ---------------------------------------------------------------------------

def process_pdf(
    file_bytes: bytes,
    *,
    allow_ocr: bool = True,
    min_chars_per_page: float = DEFAULT_MIN_CHARS_PER_PAGE,
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    ocr_lang: str = "spa+eng",
    ocr_max_pages: Optional[int] = None,
) -> ProcessedDocument:
    """
    Aduana completa: extracción híbrida → heal → chunks con overlap.

    El campo ``text`` es el documento cosido completo (para preview / clip).
    ``chunks`` es lo que debe enviarse al LLM ítem por ítem hacia banco_preguntas.
    """
    if not file_bytes:
        return ProcessedDocument(
            text="",
            method="empty",
            warnings=["PDF vacío (0 bytes)."],
        )

    raw, method, pages, warnings = extract_pdf_text_hybrid(
        file_bytes,
        min_chars_per_page=min_chars_per_page,
        allow_ocr=allow_ocr,
        ocr_lang=ocr_lang,
        ocr_max_pages=ocr_max_pages,
    )
    healed = heal_text(raw)
    chunks = chunk_text(
        healed,
        chunk_tokens=chunk_tokens,
        overlap_ratio=overlap_ratio,
    )
    density = _alphanumeric_density(healed, pages or 1)

    return ProcessedDocument(
        text=healed,
        chunks=chunks,
        method=method,
        page_count=pages,
        chars_per_page=round(density, 1),
        used_ocr=method in {"ocr", "hybrid"},
        warnings=warnings,
        meta={
            "raw_chars": len(raw or ""),
            "healed_chars": len(healed or ""),
            "n_chunks": len(chunks),
            "chunk_tokens_target": chunk_tokens,
            "overlap_ratio": max(0.15, float(overlap_ratio)),
            "est_tokens_total": _estimate_tokens(healed) if healed else 0,
        },
    )


# Alias amigable para el frontend legacy
def extract_text_from_pdf(file_bytes: bytes, **kwargs: Any) -> str:
    """Compatibilidad con main_app: devuelve solo el texto cosido."""
    return process_pdf(file_bytes, **kwargs).text
