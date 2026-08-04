"""Reglas de puntaje UNA — portadas desde TutorEngine (neuro_plataforma)."""

from __future__ import annotations

import random
from typing import Any, Optional

PUNTOS_CORRECTA = 10
PUNTOS_EN_BLANCO = 2
PUNTOS_INCORRECTA = 0

_LETTERS = ("A", "B", "C", "D", "E")


def normalizar_marcada(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if text not in {"A", "B", "C", "D", "E"}:
        return None
    return text


def shuffle_alternativas(
    alternativas: dict[str, Any] | None,
    alternativa_correcta: str,
    *,
    rng: Optional[random.Random] = None,
) -> tuple[dict[str, str], str, dict[str, str]]:
    """Mezcla A–E en runtime y remapea la clave correcta.

    Returns:
        (nuevas_alternativas, nueva_correcta, map_display_to_banco)
        donde map_display_to_banco[letra_mostrada] = letra_original_en_bóveda.
    """
    src = alternativas if isinstance(alternativas, dict) else {}
    pairs = [(k, str(src.get(k, "") or "")) for k in _LETTERS]
    mixer = rng or random.SystemRandom()
    mixer.shuffle(pairs)

    nuevas: dict[str, str] = {}
    display_to_banco: dict[str, str] = {}
    old_correct = str(alternativa_correcta or "").strip().upper() or "A"
    new_correct = "A"

    for new_letter, (old_letter, text) in zip(_LETTERS, pairs):
        nuevas[new_letter] = text
        display_to_banco[new_letter] = old_letter
        if old_letter == old_correct:
            new_correct = new_letter

    return nuevas, new_correct, display_to_banco


def shuffle_pregunta(
    pregunta: dict[str, Any],
    *,
    rng: Optional[random.Random] = None,
) -> dict[str, Any]:
    """Copia una pregunta con alternativas aleatorizadas (validación por letra remapeada)."""
    row = dict(pregunta)
    alts_banco = dict(row.get("alternativas") or {})
    correcta_banco = str(row.get("alternativa_correcta") or "").upper()
    nuevas, nueva_ok, mapping = shuffle_alternativas(
        alts_banco, correcta_banco, rng=rng
    )
    row["alternativas"] = nuevas
    row["alternativa_correcta"] = nueva_ok
    row["alternativa_correcta_banco"] = correcta_banco
    row["shuffle_map"] = mapping  # display → bóveda
    return row


def shuffle_bloque(
    preguntas: list[dict[str, Any]],
    *,
    seed: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Aleatoriza alternativas de cada ítem (seed opcional para tests)."""
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    return [shuffle_pregunta(q, rng=rng) for q in preguntas]


def puntaje_ponderado_item(puntos_base: int, factor_ponderacion: float) -> float:
    return float(round(int(puntos_base) * float(factor_ponderacion), 3))


def evaluar_alternativa(
    *,
    alternativa_marcada: Optional[str],
    alternativa_correcta: str,
    factor_ponderacion: float,
) -> dict[str, Any]:
    """10 correcta · 2 en blanco · 0 incorrecta."""
    marcada = normalizar_marcada(alternativa_marcada)
    correcta = str(alternativa_correcta or "").strip().upper()
    factor = float(factor_ponderacion)

    if marcada is None:
        return {
            "alternativa_marcada": None,
            "es_correcta": None,
            "puntaje_obtenido": PUNTOS_EN_BLANCO,
            "puntaje_ponderado": puntaje_ponderado_item(PUNTOS_EN_BLANCO, factor),
        }

    es_ok = marcada == correcta
    puntos = PUNTOS_CORRECTA if es_ok else PUNTOS_INCORRECTA
    return {
        "alternativa_marcada": marcada,
        "es_correcta": es_ok,
        "puntaje_obtenido": puntos,
        "puntaje_ponderado": puntaje_ponderado_item(puntos, factor),
    }


def evaluar_bloque(
    preguntas: list[dict[str, Any]],
    respuestas: dict[str, str],
) -> dict[str, Any]:
    correctas = 0
    incorrectas = 0
    en_blanco = 0
    bruto = 0.0
    ponderado = 0.0

    for q in preguntas:
        qid = str(q["id_pregunta"])
        ev = evaluar_alternativa(
            alternativa_marcada=respuestas.get(qid),
            alternativa_correcta=q["alternativa_correcta"],
            factor_ponderacion=float(q.get("factor_ponderacion", 1.0)),
        )
        bruto += float(ev["puntaje_obtenido"])
        ponderado += float(ev["puntaje_ponderado"])
        if ev["es_correcta"] is True:
            correctas += 1
        elif ev["es_correcta"] is False:
            incorrectas += 1
        else:
            en_blanco += 1

    total = len(preguntas)
    return {
        "total": total,
        "correctas": correctas,
        "incorrectas": incorrectas,
        "en_blanco": en_blanco,
        "puntaje_bruto": float(round(bruto, 3)),
        "puntaje_ponderado": float(round(ponderado, 3)),
        "aciertos_pct": int(round((correctas / total) * 100)) if total else 0,
    }
