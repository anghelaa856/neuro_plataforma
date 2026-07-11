import sys
import uuid
from dataclasses import asdict

import requests

from app.database.db_manager import db_manager
from app.ml.anomaly_service import TimeAnomalyService
from app.ml.interval_policy import IntervalPolicyService
from app.ml.nlp_service import SemanticNLPService
from app.services.evaluation_service import evaluate_answer


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_ui_health() -> None:
    resp = requests.get("http://127.0.0.1:8501/_stcore/health", timeout=5)
    assert_true(resp.status_code == 200, f"UI health endpoint failed: {resp.status_code}")
    body = resp.text.strip().lower()
    assert_true(body in {"ok", "healthy", "running"}, f"Unexpected UI health payload: {body}")


def check_ml_services() -> None:
    nlp = SemanticNLPService()
    anomaly = TimeAnomalyService()
    policy = IntervalPolicyService()

    nlp_result = nlp.grade_answer(
        reference_text="La depreciacion distribuye el costo de un activo en su vida util.",
        student_text="La depreciacion reparte el costo del activo durante su vida util.",
    )
    assert_true(0.0 <= nlp_result.score_0_5 <= 5.0, "NLP score out of range")

    an_result = anomaly.evaluate_response_time(response_time_ms=8200)
    assert_true(an_result.state in {"Normal", "Anomalia"}, "Unexpected anomaly state")

    decision = policy.recommend_interval(
        score_0_5=nlp_result.score_0_5,
        is_anomaly=an_result.is_anomaly,
        repetitions=2,
        easiness=2.5,
        interval_days=6,
    )
    assert_true(decision.interval_days >= 1, "Interval policy returned invalid interval")

    decided_mode_new = policy.decide_question_mode(is_new_topic=True, interval_days=1, is_mock_mode=False)
    assert_true(decided_mode_new == "open", "New topic must default to open mode")

    decided_mode_mock = policy.decide_question_mode(is_new_topic=False, interval_days=30, is_mock_mode=True)
    assert_true(decided_mode_mock == "mcq", "Mock mode must force mcq mode")

    generated_open = nlp.generate_questions_from_text(
        content_text="La depreciacion distribuye el costo del activo durante su vida util y afecta el resultado.",
        n_questions=1,
        question_mode="open",
        source_origin="manual",
    )
    assert_true(len(generated_open) >= 1, "Open question generation returned no items")
    assert_true(generated_open[0].question_type == "open", "Open generation returned wrong question type")

    generated_mcq = nlp.generate_questions_from_text(
        content_text="El capital de trabajo es la diferencia entre activos y pasivos corrientes.",
        n_questions=1,
        question_mode="mcq",
        source_origin="pdf+manual",
    )
    assert_true(len(generated_mcq) >= 1, "MCQ generation returned no items")
    assert_true(generated_mcq[0].question_type == "mcq", "MCQ generation returned wrong question type")
    assert_true(len(generated_mcq[0].options) >= 4, "MCQ options are insufficient")


def check_db_and_pipeline_integration() -> None:
    db_manager.connect()
    db_manager.ensure_schema()

    topic_state = db_manager.fetch_topic_latest_state(tema="Validacion Integrada")
    assert_true("is_new_topic" in topic_state, "Topic state did not return expected metadata")

    eval_result = evaluate_answer(
        question_text="Define depreciacion y su impacto.",
        reference_text="La depreciacion reconoce gasto periodico y reduce valor en libros.",
        student_answer="La depreciacion reduce el valor en libros con gasto periodico.",
        response_time_ms=9100,
        previous_repetitions=1,
        previous_easiness=2.5,
        previous_interval_days=3,
    )

    run_id = str(uuid.uuid4())
    card_id = db_manager.insert_memory_card(
        area="QA-E2E",
        tema="Validacion Integrada",
        pregunta=f"Smoke test {run_id}",
        respuesta_referencia="Referencia E2E",
        respuesta_estudiante="Respuesta E2E",
        nota_ia=eval_result.nota_ia,
        auditoria_estado=eval_result.auditoria_estado,
        auditoria_tiempo_ms=eval_result.auditoria_tiempo_ms,
        intervalo_recomendado_dias=eval_result.intervalo_dias,
        plan_estudio=IntervalPolicyService.PLAN_RETENCION,
        tipo_pregunta="open",
        modo_simulacro=False,
        origen_contenido="manual",
        opciones_respuesta=[],
        indice_opcion_correcta=None,
        opcion_estudiante_indice=None,
        repetitions_count=eval_result.repetitions,
        easiness_factor=eval_result.easiness,
    )
    assert_true(card_id > 0, "DB insert did not return a valid id")

    cards = db_manager.fetch_memory_cards(limit=100)
    assert_true(len(cards) > 0, "DB fetch returned no rows")
    found = any(c.get("pregunta") == f"Smoke test {run_id}" for c in cards)
    assert_true(found, "Inserted E2E row was not found in recent DB rows")

    dashboard = db_manager.fetch_progress_dashboard(due_limit=10)
    assert_true("por_plan" in dashboard, "Dashboard missing plan distribution data")

    assert_true(eval_result.nota_ia >= 0.0, "Pipeline produced invalid nota_ia")
    assert_true(eval_result.intervalo_dias >= 1, "Pipeline produced invalid interval")


def main() -> int:
    checks = [
        ("UI Health", check_ui_health),
        ("ML Services", check_ml_services),
        ("DB + Pipeline", check_db_and_pipeline_integration),
    ]

    print("E2E Validation Checklist")
    all_ok = True
    for name, fn in checks:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as exc:
            all_ok = False
            print(f"[FAIL] {name}: {exc}")

    if all_ok:
        print("SYSTEM_STABLE=TRUE")
        return 0

    print("SYSTEM_STABLE=FALSE")
    return 1


if __name__ == "__main__":
    sys.exit(main())
