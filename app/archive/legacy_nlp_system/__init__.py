"""
Archivo del sistema legacy de flashcards + NLP + SM-2.

NO forma parte del runtime UNA (TutorEngine / práctica / simulacro / tutor socrático).
Se conserva para reutilizar lógica pedagógica en Fase 4 (remediación).

Qué vive aquí
-------------
- evaluation_service.py
- nlp_service.py
- anomaly_service.py
- interval_policy.py
- legacy_ui_tabs.py (Estudiar / Cargar / Dashboard — no montadas)
- memory_card_repository_snapshot.py (copia de referencia)

Qué sigue ACTIVO fuera de este archivo (decisión de estabilidad)
----------------------------------------------------------------
- DDL `memoria_activa` en `app.infrastructure.database.schema` (ensure_schema)
- `MemoryCardRepository` vivo en `app.infrastructure.database.repositories`
  (fachada db_manager; sin UI montada)
- `content_service._openrouter_chat` / extracción de banco UNA
  (usado por `banco_extraction_service`)
"""

from app.archive.legacy_nlp_system.anomaly_service import TimeAnomalyService
from app.archive.legacy_nlp_system.evaluation_service import (
    EvaluationResult,
    evaluate_answer,
    interval_policy,
)
from app.archive.legacy_nlp_system.interval_policy import IntervalPolicyService
from app.archive.legacy_nlp_system.nlp_service import NlpScoreResult, SemanticNLPService

__all__ = [
    "EvaluationResult",
    "evaluate_answer",
    "interval_policy",
    "IntervalPolicyService",
    "NlpScoreResult",
    "SemanticNLPService",
    "TimeAnomalyService",
]
