"""Extracción inteligente de tarjetas de estudio desde apuntes/PDF."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Literal, Optional

import requests
import certifi

from app.infrastructure.ssl_config import ssl_verify
from config.settings import settings

logger = logging.getLogger(__name__)

CardMode = Literal["concepto", "ejercicio"]
ExtractionSource = Literal["openrouter", "local-fallback"]

FORBIDDEN_AREAS = {"general", "varios", "otro", "others", "misc", "bloque"}
NAV_NOISE_PATTERNS = [
    r"\bartículo\b",
    r"\bdiscutir?\b",
    r"\bdiscusión\b",
    r"\bleer\b",
    r"\beditar\b",
    r"\bver historial\b",
    r"\bherramientas\b",
    r"\binicio\b",
    r"\bcookie[s]?\b",
    r"\biniciar sesión\b",
    r"\bcrear una cuenta\b",
    r"\bnavegación\b",
    r"\bportada\b",
    r"\bcategorías?\b",
    r"coordenadas?",
    r"^\s*\d{1,3}[°º]",
    r"latitud|longitud",
    r"wikipedia|wikimedia",
    r"^\s*\[\s*editar\s*\]",
]


@dataclass
class ExtractedCard:
    area: str
    tema: str
    pregunta: str
    respuesta_referencia: str
    modo_tarjeta: CardMode
    nivel_dificultad: int = 1
    method: str = "openrouter"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractionResult:
    cards: List[ExtractedCard]
    source: ExtractionSource
    detail: str

    @property
    def used_openrouter(self) -> bool:
        return self.source == "openrouter"


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    return cleaned


def _normalize_mode(raw: Any) -> CardMode:
    value = str(raw or "").strip().lower()
    if value in {"ejercicio", "exercise", "math", "problema", "calculo", "cálculo"}:
        return "ejercicio"
    return "concepto"


def _normalize_label(value: str) -> str:
    return " ".join(str(value).strip().split())


def _normalize_difficulty(raw: Any) -> int:
    try:
        level = int(raw)
    except (TypeError, ValueError):
        level = 1
    return max(1, min(3, level))


def _is_forbidden_label(area: str, tema: str) -> bool:
    area_l = area.lower()
    tema_l = tema.lower()
    if area_l in FORBIDDEN_AREAS:
        return True
    if tema_l.startswith("bloque") or tema_l in FORBIDDEN_AREAS:
        return True
    if re.match(r"^bloque\s*\d+", tema_l):
        return True
    return False


def _clean_source_text(text: str) -> str:
    """Elimina basura típica de navegación web / PDF scraped."""
    lines = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw:
            continue
        if len(raw) <= 2:
            continue
        lower = raw.lower()
        if any(re.search(pat, lower, flags=re.I) for pat in NAV_NOISE_PATTERNS):
            continue
        if re.fullmatch(r"[\W\d_]+", raw):
            continue
        lines.append(raw)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _openrouter_chat(system_prompt: str, user_prompt: str, max_tokens: int = 3500) -> str:
    key = (settings.openrouter_api_key or "").strip()
    if not key or key.lower().startswith("your_"):
        raise RuntimeError(
            "OPENROUTER_API_KEY ausente o placeholder en .env "
            "(debe ser una clave real de OpenRouter)."
        )

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": settings.openrouter_site_url,
        "X-Title": settings.openrouter_app_name,
    }
    payload: Dict[str, Any] = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.35,
        "max_tokens": max_tokens,
    }
    logger.info(
        "OpenRouter extracción: modelo=%s, prompt_chars=%s",
        settings.openrouter_model,
        len(user_prompt),
    )
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=120,
        verify=ssl_verify(),  # truststore (CA del SO) o fallback certifi.where()
    )
    if response.status_code >= 400:
        body = response.text[:400]
        raise RuntimeError(f"OpenRouter HTTP {response.status_code}: {body}")
    data = response.json()
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("OpenRouter no devolvió choices")
    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError("OpenRouter devolvió contenido vacío")
    return content


def _build_system_prompt(max_cards: int) -> str:
    return f"""Eres un Diseñador Instruccional Experto que crea experiencias de estudio adaptativas estilo Duolingo para exámenes de admisión universitaria.

Tu ÚNICA salida debe ser JSON válido (sin markdown, sin comentarios) con esta forma exacta:
{{
  "tarjetas": [
    {{
      "area": "Historia Universal",
      "tema": "Descubrimiento de América",
      "pregunta": "...",
      "respuesta_referencia": "...",
      "modo_tarjeta": "concepto",
      "nivel_dificultad": 1
    }}
  ]
}}

REGLAS ESTRICTAS:

1) LIMPIEZA DE DATOS
- Ignora menús de navegación, breadcrumbs, "Artículo", "Discusión", "Leer", "Editar", cookies, login, coordenadas geográficas, pies de página, publicidad y basura web.
- Extrae SOLO contenido académico útil para estudiar.

2) PRECISIÓN DE ETIQUETAS
- "area" debe ser una materia real y específica (ej. Historia Universal, Matemáticas, Contabilidad, Literatura, Biología).
- "tema" debe ser preciso y estable (ej. Descubrimiento de América, Ecuaciones lineales, Depreciación).
- PROHIBIDO usar: "General", "Bloque", "Bloque 1", "Varios", "Tema 1", "Contenido".

3) VARIEDAD PEDAGÓGICA (nunca monótono)
Genera aproximadamente {max_cards} tarjetas con formatos DISTINTOS entre sí. Mezcla obligatoriamente:
- Memoria directa: "¿Quién hizo X?" / "¿En qué año ocurrió Y?"
- Cloze (completar): "Colón zarpó del Puerto de ______ en el año ______."
- Comprensión: "Explica con tus palabras la diferencia entre X e Y."
- Análisis crítico: "¿Por qué fue importante X? Argumenta con 2 ideas del texto."
- Si hay matemática/lógica: al menos una tarjeta modo_tarjeta="ejercicio" con resultado verificable.
No repitas el mismo encabezado tipo "Resume o resuelve el siguiente contenido".

4) NIVELES DE DIFICULTAD (Duolingo)
- nivel_dificultad es entero 1, 2 o 3:
  1 = Básico / Recuerdo
  2 = Intermedio / Aplicación
  3 = Avanzado / Análisis
- Distribuye niveles: incluye varios 1, varios 2 y al menos uno 3 si el texto lo permite.

5) MODO DE TARJETA
- "concepto": teoría, definiciones, comprensión, cloze histórico/literario.
- "ejercicio": cálculo, resultado exacto, lógica cuantitativa.

6) FIDELIDAD
- No inventes hechos fuera del texto.
- respuesta_referencia debe ser breve, correcta y útil para calificar.
- Genera cerca de {max_cards} tarjetas de alta calidad (no relleno).
"""


def _parse_cards_payload(payload: Any, method: str) -> List[ExtractedCard]:
    if isinstance(payload, dict):
        items = payload.get("tarjetas") or payload.get("cards") or payload.get("questions") or []
    elif isinstance(payload, list):
        items = payload
    else:
        raise ValueError("JSON de extracción inválido")

    if not isinstance(items, list):
        raise ValueError("El campo de tarjetas debe ser una lista")

    cards: List[ExtractedCard] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        area = _normalize_label(item.get("area", ""))
        tema = _normalize_label(item.get("tema", ""))
        pregunta = str(item.get("pregunta", "")).strip()
        respuesta = str(
            item.get("respuesta_referencia")
            or item.get("respuesta")
            or item.get("reference_answer")
            or ""
        ).strip()
        modo = _normalize_mode(item.get("modo_tarjeta") or item.get("modo"))
        nivel = _normalize_difficulty(item.get("nivel_dificultad") or item.get("nivel") or 1)

        if len(area) < 2 or len(tema) < 2 or len(pregunta) < 8 or len(respuesta) < 1:
            continue
        if _is_forbidden_label(area, tema):
            logger.warning("Tarjeta descartada por etiqueta prohibida: area=%s tema=%s", area, tema)
            continue
        if pregunta.lower().startswith("resume o resuelve"):
            continue

        cards.append(
            ExtractedCard(
                area=area,
                tema=tema,
                pregunta=pregunta,
                respuesta_referencia=respuesta,
                modo_tarjeta=modo,
                nivel_dificultad=nivel,
                method=method,
            )
        )

    if not cards:
        raise ValueError("La IA no devolvió tarjetas válidas tras el filtrado")
    return cards


def _fallback_local_cards(content_text: str, max_cards: int) -> List[ExtractedCard]:
    """
    Fallback de emergencia (NO es el flujo ideal).
    Intenta crear cloze/memoria simples sin etiquetas 'General/Bloque'.
    """
    sentences = [
        s.strip()
        for s in re.split(r"(?<=[\.\!\?])\s+", content_text)
        if len(s.strip()) >= 50
    ]
    cards: List[ExtractedCard] = []
    formats = ("memoria", "cloze", "comprension")
    for idx, sentence in enumerate(sentences[:max_cards]):
        words = [w for w in re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]+", sentence) if len(w) > 3]
        if len(words) < 4:
            continue
        key = words[min(2, len(words) - 1)]
        fmt = formats[idx % len(formats)]
        if fmt == "memoria":
            pregunta = f"Según el texto, ¿qué se afirma sobre «{key}»?"
            respuesta = sentence
            nivel = 1
        elif fmt == "cloze":
            pregunta = sentence.replace(key, "______", 1)
            if "______" not in pregunta:
                pregunta = f"Completa: {sentence[:80]} ______"
            respuesta = key
            nivel = 2
        else:
            pregunta = f"Explica con tus palabras la idea central de: «{sentence[:120]}»"
            respuesta = sentence
            nivel = 2

        looks_math = bool(re.search(r"\d+\s*[\+\-\*/=]|ecuaci|resuelve|calcul", sentence, re.I))
        cards.append(
            ExtractedCard(
                area="Contenido académico",
                tema="Extracción local de emergencia",
                pregunta=pregunta,
                respuesta_referencia=respuesta,
                modo_tarjeta="ejercicio" if looks_math else "concepto",
                nivel_dificultad=nivel,
                method="local-fallback",
            )
        )

    if not cards:
        raise RuntimeError(
            "Fallback local sin material usable. Configura OPENROUTER_API_KEY y reintenta."
        )
    return cards[:max_cards]


def extract_study_cards(
    content_text: str,
    max_cards: int = 10,
    career_hint: Optional[str] = None,
) -> ExtractionResult:
    """
    Extrae ~10 tarjetas con OpenRouter (Diseñador Instruccional / Duolingo).
    Si falla la API, degrada a fallback local y lo declara explícitamente.
    """
    text = (content_text or "").strip()
    if len(text) < 40:
        raise ValueError("El contenido es demasiado corto para extracción inteligente.")

    cleaned = _clean_source_text(text)
    if len(cleaned) < 40:
        cleaned = text
    clipped = cleaned[:12000]

    career_line = (
        f"Carrera objetivo del estudiante (prioriza áreas relevantes): {career_hint.strip()}."
        if career_hint and career_hint.strip()
        else "No hay carrera objetivo explícita; deduce área y tema solo del contenido académico."
    )

    system_prompt = _build_system_prompt(max_cards=max_cards)
    user_prompt = f"{career_line}\n\nTexto fuente (ya filtrado de basura web cuando fue posible):\n{clipped}"

    try:
        raw = _openrouter_chat(system_prompt=system_prompt, user_prompt=user_prompt)
        payload = json.loads(_strip_code_fences(raw))
        cards = _parse_cards_payload(payload, method="openrouter")[:max_cards]
        detail = (
            f"Fuente: OpenRouter ({settings.openrouter_model}). "
            f"Tarjetas válidas: {len(cards)}."
        )
        logger.info(detail)
        return ExtractionResult(cards=cards, source="openrouter", detail=detail)
    except Exception as exc:
        detail = (
            f"Fuente: FALLBACK LOCAL (OpenRouter no disponible). "
            f"Motivo: {type(exc).__name__}: {exc}"
        )
        logger.warning(detail)
        cards = _fallback_local_cards(clipped, max_cards=max_cards)
        return ExtractionResult(cards=cards, source="local-fallback", detail=detail)


def cards_to_table_rows(cards: List[ExtractedCard]) -> List[Dict[str, Any]]:
    """Filas listas para mostrar en Streamlit."""
    return [card.to_dict() for card in cards]


@dataclass
class MutationResult:
    pregunta_mutada: str
    method: str
    detail: str


def mutate_question_for_review(
    pregunta_original: str,
    respuesta_referencia: str,
    modo_tarjeta: str = "concepto",
    nivel_dificultad: int = 1,
) -> MutationResult:
    """
    Reescribe la pregunta para repasos (anti-memorización loro).
    La evaluación sigue usando respuesta_referencia original.
    """
    original = (pregunta_original or "").strip()
    reference = (respuesta_referencia or "").strip()
    if not original or not reference:
        return MutationResult(
            pregunta_mutada=original or reference,
            method="noop",
            detail="Sin texto suficiente para mutar.",
        )

    system_prompt = (
        "Eres un diseñador instruccional de aprendizaje adaptativo (estilo Duolingo). "
        "Tu tarea es evitar la memorización loro. "
        "Responde SOLO JSON válido: {\"pregunta_mutada\":\"...\"}. "
        "Toma la pregunta original y la respuesta de referencia. "
        "Reescribe la pregunta desde un ángulo completamente distinto, o como un caso "
        "aplicado breve, para evaluar si el alumno entiende el contexto completo sin "
        "memorizar la frase exacta. "
        "Mantén el mismo conocimiento evaluado y el mismo nivel de exigencia. "
        "No reveles la respuesta en la pregunta. No inventes hechos nuevos."
    )
    user_prompt = (
        f"modo_tarjeta: {modo_tarjeta}\n"
        f"nivel_dificultad: {nivel_dificultad}\n"
        f"pregunta_original: {original}\n"
        f"respuesta_referencia: {reference}\n"
    )

    try:
        raw = _openrouter_chat(system_prompt=system_prompt, user_prompt=user_prompt, max_tokens=400)
        payload = json.loads(_strip_code_fences(raw))
        mutated = str(payload.get("pregunta_mutada") or "").strip()
        if len(mutated) < 8:
            raise ValueError("pregunta_mutada vacía o inválida")
        if mutated.lower() == original.lower():
            # Forzar un ángulo mínimo distinto en fallback local.
            mutated = (
                f"Caso aplicado: con base en lo que sabes, responde de forma distinta a "
                f"esta idea (sin repetir la frase original):\n{original}"
            )
        detail = f"Pregunta mutada vía OpenRouter ({settings.openrouter_model})."
        logger.info(detail)
        return MutationResult(pregunta_mutada=mutated, method="openrouter-mutate", detail=detail)
    except Exception as exc:
        # Mutación local mínima si no hay API.
        if modo_tarjeta == "ejercicio":
            mutated = (
                "Resuelve el mismo concepto/problema planteado desde este enunciado "
                f"reformulado:\n{original}"
            )
        else:
            mutated = (
                "Explica la idea con tus palabras en un contexto nuevo "
                f"(no copies la pregunta literal):\n¿Qué principio o hecho sostiene esto?\n"
                f"Pista de tema: {original[:120]}"
            )
        detail = f"Mutación local (OpenRouter no disponible): {type(exc).__name__}: {exc}"
        logger.warning(detail)
        return MutationResult(pregunta_mutada=mutated, method="local-mutate", detail=detail)
