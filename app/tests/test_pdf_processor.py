"""Tests unitarios de la Aduana de Limpieza (sin binarios OCR)."""

from __future__ import annotations

from app.services.pdf_processor import chunk_text, heal_text, needs_ocr, process_pdf


def test_heal_hyphenation_and_newlines() -> None:
    raw = (
        "El sistema respirato-\n"
        "rio incluye pulmones.\n"
        "La hemoglobina\n"
        "transporta oxígeno.\n\n"
        "Nuevo párrafo sobre riñones.\n"
        "12\n"
        "Academia Preuniversitaria Elite\n"
        "https://ejemplo.com/apuntes\n"
        "página 3 de 40\n"
    )
    healed = heal_text(raw)
    assert "respiratorio" in healed
    assert "hemoglobina transporta oxígeno" in healed
    assert "\n\n" in healed
    assert "Nuevo párrafo" in healed
    assert "https://ejemplo.com" not in healed
    assert "página 3" not in healed.lower()
    assert "Academia Preuniversitaria" not in healed


def test_chunk_overlap() -> None:
    # Texto largo artificial
    para = "Concepto de anatomía. " * 80
    text = (para + "\n\n") * 6
    chunks = chunk_text(text, chunk_tokens=200, overlap_ratio=0.15)
    assert len(chunks) >= 2
    # Overlap: el final de un chunk aparece al inicio del siguiente
    overlap_hits = 0
    for a, b in zip(chunks, chunks[1:]):
        tail = a[-40:]
        if tail and tail in b:
            overlap_hits += 1
    assert overlap_hits >= 1


def test_needs_ocr_density() -> None:
    assert needs_ocr("", 3) is True
    assert needs_ocr("x" * 500, 2) is False
    assert needs_ocr("ab", 5) is True


def test_process_pdf_empty() -> None:
    doc = process_pdf(b"", allow_ocr=False)
    assert doc.method == "empty"
    assert doc.chunks == []


def main() -> None:
    test_heal_hyphenation_and_newlines()
    test_chunk_overlap()
    test_needs_ocr_density()
    test_process_pdf_empty()
    print("pdf_processor smoke OK")


if __name__ == "__main__":
    main()
