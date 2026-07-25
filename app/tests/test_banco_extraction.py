"""Validación de esquemas tipados / hash / slug del pipeline bóveda UNA."""

from __future__ import annotations

from app.infrastructure.database.banco_repository import hash_contenido_pregunta, slugify_tema
from app.services.banco_extraction_service import (
    ExtraccionBancoPayload,
    ItemBancoGenerado,
    ItemValidationError,
)


def test_item_mcq_valido() -> None:
    item = ItemBancoGenerado.from_raw(
        {
            "tema_especifico": "Tejido epitelial",
            "enunciado": "¿Cuál es la función principal del epitelio de revestimiento?",
            "alternativas": {
                "A": "Contracción",
                "B": "Protección y barrera",
                "C": "Conducción nerviosa",
                "D": "Hematopoyesis",
                "E": "Osmorregulación renal",
            },
            "alternativa_correcta": "b",
            "justificacion": "El epitelio de revestimiento actúa como barrera protectora.",
        }
    )
    assert item.alternativa_correcta == "B"
    assert len(item.alternativas.as_dict()) == 5


def test_item_rechaza_tema_basura() -> None:
    try:
        ItemBancoGenerado.from_raw(
            {
                "tema_especifico": "General",
                "enunciado": "Pregunta suficientemente larga para pasar el mínimo.",
                "alternativas": {k: f"Opción {k}" for k in "ABCDE"},
                "alternativa_correcta": "A",
                "justificacion": "Porque sí, explicación mínima.",
            }
        )
        raise AssertionError("debió rechazar tema General")
    except ItemValidationError:
        pass


def test_item_acepta_lista_de_alternativas() -> None:
    item = ItemBancoGenerado.from_raw(
        {
            "tema_especifico": "Mitosis",
            "enunciado": "¿En qué fase se separan las cromátidas hermanas?",
            "alternativas": ["Profase", "Metafase", "Anafase", "Telofase", "Interfase"],
            "alternativa_correcta": "C",
            "justificacion": "En anafase se separan las cromátidas hacia polos opuestos.",
        }
    )
    assert item.alternativas.C == "Anafase"


def test_payload_alias_preguntas() -> None:
    payload = ExtraccionBancoPayload.from_raw(
        {
            "preguntas": [
                {
                    "tema_especifico": "Ácidos nucleicos",
                    "enunciado": "¿Qué base nitrogenada es exclusiva del ARN?",
                    "alternativas": {k: f"x{k}" for k in "ABCDE"},
                    "alternativa_correcta": "A",
                    "justificacion": "El uracilo reemplaza a la timina en el ARN.",
                }
            ]
        }
    )
    assert len(payload.items) == 1


def test_slug_and_hash_estables() -> None:
    assert slugify_tema("Tejido Epitelial") == "tejido-epitelial"
    h1 = hash_contenido_pregunta(
        "Enunciado  uno",
        {k: f"Alt {k}" for k in "ABCDE"},
        "A",
    )
    h2 = hash_contenido_pregunta(
        "Enunciado uno",
        {k: f"Alt  {k}" for k in "ABCDE"},
        "a",
    )
    assert h1 == h2
    assert len(h1) == 64


def main() -> None:
    test_item_mcq_valido()
    test_item_rechaza_tema_basura()
    test_item_acepta_lista_de_alternativas()
    test_payload_alias_preguntas()
    test_slug_and_hash_estables()
    print("banco_extraction smoke OK")


if __name__ == "__main__":
    main()
