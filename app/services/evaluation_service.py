"""Pipeline de evaluación: NLP + anomalías + política de intervalo + tutoría."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from app.ml.anomaly_service import TimeAnomalyService
from app.ml.interval_policy import IntervalPolicyService
from app.ml.nlp_service import NlpScoreResult, SemanticNLPService


@dataclass
class EvaluationResult:
    nota_ia: float
    auditoria_estado: str
    auditoria_tiempo_ms: int
    intervalo_dias: int
    nlp_method: str
    anomaly_method: str
    interval_strategy: str
    quality: int
    repetitions: int
    easiness: float
    key_points: List[str]
    conceptual_errors: List[str]
    writing_suggestion: str
    feedback_method: str
    modo_tarjeta: str = "concepto"


nlp_service = SemanticNLPService()
anomaly_service = TimeAnomalyService()
interval_policy = IntervalPolicyService()


def _extract_numbers(text: str) -> List[float]:
    matches = re.findall(r"[-+]?\d+(?:[.,]\d+)?", text or "")
    numbers: List[float] = []
    for match in matches:
        try:
            numbers.append(float(match.replace(",", ".")))
        except ValueError:
            continue
    return numbers


def _normalize_exact(text: str) -> str:
    cleaned = (text or "").strip().lower()
    cleaned = cleaned.replace(",", ".")
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def grade_exercise_answer(reference_text: str, student_text: str) -> NlpScoreResult:
    """
    Evaluación estricta para ejercicios: prioriza resultado exacto / numérico.
    """
    ref = (reference_text or "").strip()
    stu = (student_text or "").strip()
    if not ref or not stu:
        return NlpScoreResult(
            score_0_5=0.0,
            similarity_0_1=0.0,
            method="exercise-empty",
            model_name="exact-match",
        )

    if _normalize_exact(ref) == _normalize_exact(stu):
        return NlpScoreResult(
            score_0_5=5.0,
            similarity_0_1=1.0,
            method="exercise-exact",
            model_name="exact-match",
        )

    ref_nums = _extract_numbers(ref)
    stu_nums = _extract_numbers(stu)

    if ref_nums and stu_nums:
        # Compara el último número (suele ser el resultado final).
        target = ref_nums[-1]
        given = stu_nums[-1]
        if abs(target - given) < 1e-9:
            return NlpScoreResult(
                score_0_5=5.0,
                similarity_0_1=1.0,
                method="exercise-numeric-exact",
                model_name="exact-match",
            )
        denom = max(abs(target), 1e-9)
        rel_err = abs(target - given) / denom
        if rel_err <= 0.01:
            return NlpScoreResult(
                score_0_5=4.0,
                similarity_0_1=0.8,
                method="exercise-numeric-near",
                model_name="exact-match",
            )
        if rel_err <= 0.05:
            return NlpScoreResult(
                score_0_5=2.0,
                similarity_0_1=0.4,
                method="exercise-numeric-far",
                model_name="exact-match",
            )
        return NlpScoreResult(
            score_0_5=0.5,
            similarity_0_1=0.1,
            method="exercise-numeric-wrong",
            model_name="exact-match",
        )

    # Sin números claros: exige solapamiento alto; si no, nota baja.
    overlap = nlp_service._token_overlap_similarity(ref, stu)
    if overlap >= 0.92:
        score = 4.5
    elif overlap >= 0.8:
        score = 3.0
    else:
        score = round(overlap * 2.5, 2)  # más estricto que semántica general
    return NlpScoreResult(
        score_0_5=float(min(5.0, score)),
        similarity_0_1=round(overlap, 4),
        method="exercise-strict-overlap",
        model_name="exact-match",
    )


def _grade_by_mode(reference_text: str, student_text: str, modo_tarjeta: str) -> NlpScoreResult:
    mode = (modo_tarjeta or "concepto").strip().lower()
    if mode == "ejercicio":
        return grade_exercise_answer(reference_text=reference_text, student_text=student_text)
    return nlp_service.grade_answer(reference_text=reference_text, student_text=student_text)


def evaluate_answer(
    question_text: str,
    reference_text: str,
    student_answer: str,
    response_time_ms: int,
    previous_repetitions: int,
    previous_easiness: float,
    previous_interval_days: int,
    modo_tarjeta: str = "concepto",
) -> EvaluationResult:
    """Ejecuta pipeline NLP/ejercicio + anomalías + política de intervalo + tutoría."""
    nlp_result = _grade_by_mode(
        reference_text=reference_text,
        student_text=student_answer,
        modo_tarjeta=modo_tarjeta,
    )
    anomaly_result = anomaly_service.evaluate_response_time(response_time_ms=response_time_ms)
    interval_decision = interval_policy.recommend_interval(
        score_0_5=nlp_result.score_0_5,
        is_anomaly=anomaly_result.is_anomaly,
        repetitions=previous_repetitions,
        easiness=previous_easiness,
        interval_days=previous_interval_days,
    )
    feedback = nlp_service.generate_tutoring_feedback(
        question_text=question_text,
        reference_text=reference_text,
        student_text=student_answer,
    )

    return EvaluationResult(
        nota_ia=round(nlp_result.score_0_5, 1),
        auditoria_estado=anomaly_result.state,
        auditoria_tiempo_ms=response_time_ms,
        intervalo_dias=interval_decision.interval_days,
        nlp_method=nlp_result.method,
        anomaly_method=anomaly_result.method,
        interval_strategy=interval_decision.strategy,
        quality=interval_decision.quality,
        repetitions=interval_decision.repetitions,
        easiness=interval_decision.easiness,
        key_points=feedback.key_points,
        conceptual_errors=feedback.conceptual_errors,
        writing_suggestion=feedback.writing_suggestion,
        feedback_method=feedback.method,
        modo_tarjeta=(modo_tarjeta or "concepto").strip().lower() or "concepto",
    )
