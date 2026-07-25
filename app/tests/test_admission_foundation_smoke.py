"""Smoke tests offline: DDL parser, seed blueprint, TutorEngine ranking."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from app.infrastructure.database.admission_schema import load_admission_ddl, _split_sql_statements
from app.infrastructure.database.seed_catalogo_materias import MATERIAS_BIOMEDICAS, _validate_blueprint
from app.services.tutor_engine import TutorEngine


def main() -> None:
    sql = load_admission_ddl()
    stmts = _split_sql_statements(sql)
    assert len(stmts) > 10
    assert any("CREATE TABLE IF NOT EXISTS catalogo_materias" in s for s in stmts)
    assert any("CREATE TABLE IF NOT EXISTS historial_intentos" in s for s in stmts)
    print(f"DDL OK — {len(stmts)} sentencias")

    _validate_blueprint(MATERIAS_BIOMEDICAS)
    techo = sum(
        10 * m["cantidad_preguntas"] * m["factor_ponderacion"] for m in MATERIAS_BIOMEDICAS
    )
    assert abs(techo - 3000) < 0.05, techo
    print(f"Seed blueprint OK — techo={techo:.3f}")

    engine = TutorEngine.__new__(TutorEngine)
    engine.weak_tema_boost = 3.0
    engine.weak_pregunta_boost = 2.0

    # Muestreo estratificado
    pool = pd.DataFrame(
        {
            "id_pregunta": list(range(1, 11)),
            "tema_id": [1] * 5 + [2] * 5,
            "tema_nombre": ["A"] * 5 + ["B"] * 5,
            "enunciado": ["x" * 12] * 10,
            "alternativas": [{}] * 10,
            "alternativa_correcta": ["A"] * 10,
        }
    )
    sample = engine._sample_estratificado_materia(
        pool=pool,
        cupo=6,
        rng=np.random.default_rng(42),
        debilidad_temas={2: 0.9},
        debilidad_preguntas={},
    )
    assert len(sample) == 6
    print(f"Estratificado OK — share tema débil={(sample['tema_id'] == 2).mean():.2f}")

    # Ranking SRS práctica
    banco = pd.DataFrame(
        {
            "id_pregunta": [1, 2, 3, 4],
            "tema_id": [10, 10, 10, 10],
            "tema_nombre": ["T"] * 4,
            "materia_id": [7] * 4,
            "materia_codigo": [7] * 4,
            "materia_nombre": ["Biología y Anatomía"] * 4,
            "factor_ponderacion": [7.816] * 4,
            "enunciado": ["pregunta " + str(i) * 8 for i in range(4)],
            "alternativas": [{"A": "1", "B": "2", "C": "3", "D": "4", "E": "5"}] * 4,
            "alternativa_correcta": ["A"] * 4,
        }
    )
    hoy = date.today()
    stats = pd.DataFrame(
        {
            "pregunta_id": [1, 2, 3],
            "n_intentos": [5, 4, 2],
            "tasa_error": [0.8, 0.25, 0.0],
            "ultimo_en": [
                hoy - timedelta(days=5),  # vencida (fallo → intervalo 1)
                hoy - timedelta(days=1),  # no vencida aún si racha alta
                hoy,  # hoy, correcta
            ],
            "ultimo_correcto": [False, True, True],
            "racha_aciertos": [0, 3, 2],
        }
    )
    ranked = engine._rank_practica_srs(
        banco=banco,
        stats=stats,
        hoy=hoy,
        rng=np.random.default_rng(1),
    )
    assert ranked.iloc[0]["id_pregunta"] == 1  # vencida + alta error primero
    assert 4 in set(ranked["id_pregunta"])  # nunca vista incluida
    print("Práctica SRS OK — top=", ranked["id_pregunta"].tolist(), ranked["motivo_prioridad"].tolist())
    print("ALL SMOKE PASSED")


if __name__ == "__main__":
    main()
