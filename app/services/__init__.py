"""Servicios de aplicación (orquestación de dominio + infraestructura)."""

from app.services.banco_extraction_service import (
    BancoIngestionResult,
    BancoExtractionService,
    ItemBancoGenerado,
    banco_extraction_service,
    extract_banco_preguntas_from_chunks,
)
from app.services.content_service import (
    ExtractedCard,
    ExtractionResult,
    extract_banco_from_chunks,
    extract_study_cards,
    mutate_question_for_review,
)
from app.services.evaluation_service import EvaluationResult, evaluate_answer
from app.services.pdf_processor import ProcessedDocument, chunk_text, heal_text, process_pdf
from app.services.simulacro_engine import (
    BancoInsuficienteError,
    CatalogoInvalidoError,
    PreguntaSimulacro,
    SimulacroEngine,
    SimulacroGenerado,
    simulacro_engine,
)
from app.services.tutor_engine import (
    PracticaEnfocada,
    PreguntaTutor,
    SimulacroOficial,
    TutorEngine,
    tutor_engine,
)

__all__ = [
    "EvaluationResult",
    "evaluate_answer",
    "ExtractedCard",
    "ExtractionResult",
    "extract_study_cards",
    "extract_banco_from_chunks",
    "mutate_question_for_review",
    "BancoIngestionResult",
    "BancoExtractionService",
    "ItemBancoGenerado",
    "banco_extraction_service",
    "extract_banco_preguntas_from_chunks",
    "ProcessedDocument",
    "chunk_text",
    "heal_text",
    "process_pdf",
    "BancoInsuficienteError",
    "CatalogoInvalidoError",
    "PreguntaSimulacro",
    "SimulacroEngine",
    "SimulacroGenerado",
    "simulacro_engine",
    "PracticaEnfocada",
    "PreguntaTutor",
    "SimulacroOficial",
    "TutorEngine",
    "tutor_engine",
]
