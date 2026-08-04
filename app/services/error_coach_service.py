"""
Coach de error (one-shot) — explica por qué falló la lógica del alumno.

Diseñado para práctica con feedback inmediato: se invoca a petición del
usuario (no automático) para controlar consumo de OpenRouter.
El tono se adapta al ``nivel_tutor`` (1 principiante → 3 avanzado).
No altera puntajes ni el ledger SRS; telemetría best-effort opcional.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple

import requests

from app.infrastructure.database.tutor_interacciones_repository import (
    TutorInteraccionInsert,
    tutor_interacciones_repository,
)
from app.infrastructure.ssl_config import ssl_verify
from config.settings import settings

logger = logging.getLogger(__name__)

_ALT_KEYS = ("A", "B", "C", "D", "E")
_MAX_RESPUESTA_TOKENS = 520
_MAX_FIELD_CHARS = 1800

NivelTutor = Literal[1, 2, 3]

NIVEL_TUTOR_LABELS: Dict[int, str] = {
    1: "Nivel 1 · Principiante",
    2: "Nivel 2 · Intermedio",
    3: "Nivel 3 · Avanzado",
}

# ---------------------------------------------------------------------------
# Perfiles de personalidad (inyección en el system prompt)
# ---------------------------------------------------------------------------

_PERFILES_PERSONALIDAD: Dict[int, str] = {
    1: """\
NIVEL DEL ALUMNO: 1 — Principiante
PERSONALIDAD DEL TUTOR:
- Empático y motivador, sin ser infantil ni condescendiente.
- Usa 1 analogía cotidiana breve (cocina, deporte, tránsito, celular, etc.) \
para anclar el concepto médico/biológico.
- Valida el esfuerzo implícito ("el error es común en este punto") sin elogios vacíos.
- Vocabulario accesible: si usas un término técnico, acláralo en la misma oración.
- Extensión: 100–170 palabras. Prioriza claridad sobre densidad.""",
    2: """\
NIVEL DEL ALUMNO: 2 — Intermedio
PERSONALIDAD DEL TUTOR:
- Analítico y técnico, tono de academía preuniversitaria seria.
- Enfócate en enseñar a descartar "preguntas trampa": distracciones, \
conceptos vecinos, palabras señuelo del enunciado.
- Explica el criterio de eliminación de la opción marcada vs. la correcta.
- Lenguaje preciso, sin analogías triviales salvo que aporten claridad real.
- Extensión: 90–150 palabras. Prioriza el método de descarte.""",
    3: """\
NIVEL DEL ALUMNO: 3 — Avanzado / perfil ingresante
PERSONALIDAD DEL TUTOR:
- Muy estricto, directo y exigente. Cero suavizadores emocionales.
- Usa terminología médica/anatómica/fisiológica avanzada cuando el ítem lo permita.
- Exige precisión absoluta: nombra el mecanismo, la estructura o la relación \
causa-efecto sin rodeos.
- Trata al alumno como casi ingresante: "en el examen esto se castiga".
- Extensión: 80–130 palabras. Cada oración debe aportar densación conceptual.""",
}

# Plantilla dinámica: `{perfil_personalidad}` se inyecta según nivel_tutor.
ERROR_COACH_SYSTEM_PROMPT = """\
Eres el Coach Cognitivo de Neuro Plataforma, un tutor preuniversitario de \
Medicina (admisión universitaria en Perú). Adaptas tu estilo al nivel del alumno.

CONTEXTO: El alumno ya vio que falló y tiene la justificación oficial del banco. \
Tu trabajo NO es repetir esa justificación como un libro. Tu trabajo es \
desmontar el error de razonamiento y dejar un ancla mental para el futuro.

{perfil_personalidad}

REGLAS DE CONTENIDO (válidas en todos los niveles)
1. Parte SIEMPRE de la alternativa que eligió el alumno: explica qué \
interpretación o atajo mental suele generar ese error.
2. Contrasta con el principio correcto que valida la respuesta oficial. \
Usa la justificación del banco como fuente de verdad; puedes parafrasearla, \
no inventes hechos clínicos/biológicos que no estén respaldados por \
enunciado + justificación.
3. Cierra con UNA "regla para el próximo examen" (máx. 1 oración), \
accionable y memorable — calibrada al nivel (más suave en N1, más exigente en N3).
4. No uses listas largas ni markdown excesivo. Español neutro latinoamericano.
5. No digas "como IA" ni digas que eres un modelo. No menciones el número \
de nivel al alumno salvo que sea natural ("a este ritmo…").
6. Si faltan datos, razona solo con lo disponible; no inventes alternativas \
ni contenidos del enunciado.

ESTRUCTURA OBLIGATORIA (usa exactamente estos encabezados):
Tu error de lógica:
[2–4 oraciones]

El concepto clave:
[2–4 oraciones]

Para la próxima:
[1 oración]
"""


@dataclass(frozen=True)
class ErrorCoachReply:
    texto: str
    model: str
    method: str  # openrouter | local-fallback
    detail: str = ""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    telemetry_id: Optional[int] = None
    nivel_tutor: int = 2


def normalize_nivel_tutor(nivel: Any) -> int:
    """Fuerza nivel a {1, 2, 3}; default 2."""
    try:
        n = int(nivel)
    except (TypeError, ValueError):
        return 2
    if n <= 1:
        return 1
    if n >= 3:
        return 3
    return 2


def calcular_nivel_desde_sesion(
    *,
    comprobadas: int,
    correctas: int,
    minimo_muestra: int = 2,
) -> int:
    """
    Fallback cold-start: nivel según % de aciertos de la sesión actual.

    - < minimo_muestra respuestas comprobadas → Nivel 2 (default)
    - < 40% → Nivel 1
    - 40–75% → Nivel 2
    - > 75% → Nivel 3
    """
    if comprobadas < max(1, int(minimo_muestra)):
        return 2
    correctas = max(0, min(int(correctas), int(comprobadas)))
    pct = (correctas / float(comprobadas)) * 100.0
    return nivel_desde_precision_pct(pct)


def nivel_desde_precision_pct(precision_pct: float) -> int:
    """Mapea un % de aciertos ya consolidado a nivel de tutor 1|2|3."""
    try:
        pct = float(precision_pct)
    except (TypeError, ValueError):
        return 2
    if pct < 40.0:
        return 1
    if pct > 75.0:
        return 3
    return 2


def build_error_coach_system_prompt(nivel_tutor: int = 2) -> str:
    """Ensambla el system prompt con el perfil de personalidad del nivel."""
    nivel = normalize_nivel_tutor(nivel_tutor)
    perfil = _PERFILES_PERSONALIDAD[nivel]
    return ERROR_COACH_SYSTEM_PROMPT.format(perfil_personalidad=perfil)


def label_nivel_tutor(nivel_tutor: int = 2) -> str:
    return NIVEL_TUTOR_LABELS[normalize_nivel_tutor(nivel_tutor)]


def _clip(value: Any, limit: int = _MAX_FIELD_CHARS) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _format_alternativas(alternativas: Dict[str, str]) -> str:
    lines = []
    for key in _ALT_KEYS:
        txt = str((alternativas or {}).get(key) or "").strip()
        if txt:
            lines.append(f"{key}) {txt}")
    return "\n".join(lines) if lines else "(sin alternativas)"


def _build_user_payload(
    *,
    enunciado: str,
    alternativas: Dict[str, str],
    alternativa_correcta: str,
    alternativa_alumno: str,
    justificacion: str,
    tema_o_materia: str,
    nivel_tutor: int,
) -> str:
    return (
        f"Nivel tutor solicitado: {nivel_tutor} "
        f"({label_nivel_tutor(nivel_tutor)})\n"
        f"Materia/tema: {_clip(tema_o_materia, 200)}\n\n"
        f"Enunciado:\n{_clip(enunciado)}\n\n"
        f"Alternativas:\n{_format_alternativas(alternativas)}\n\n"
        f"Alternativa elegida por el alumno (INCORRECTA): "
        f"{alternativa_alumno}\n"
        f"Texto de esa alternativa: "
        f"{_clip((alternativas or {}).get(alternativa_alumno, ''), 600)}\n\n"
        f"Alternativa correcta: {alternativa_correcta}\n"
        f"Texto de la correcta: "
        f"{_clip((alternativas or {}).get(alternativa_correcta, ''), 600)}\n\n"
        f"Justificación oficial del banco:\n{_clip(justificacion)}\n\n"
        "Desmonta el error del alumno aplicando estrictamente el perfil "
        "de personalidad del nivel indicado."
    )


def _fallback_local(
    *,
    alternativa_alumno: str,
    alternativa_correcta: str,
    justificacion: str,
    nivel_tutor: int,
) -> str:
    just = (justificacion or "").strip() or (
        "Revisa el enunciado y contrasta definiciones cercanas."
    )
    nivel = normalize_nivel_tutor(nivel_tutor)
    if nivel == 1:
        cierre = (
            "Piensa en el concepto como una ruta: si tomas el desvío de la "
            f"{alternativa_alumno}, llegas a otro tema; confirma el camino "
            f"que lleva a la {alternativa_correcta} antes de marcar."
        )
        intro = (
            f"Elegiste la {alternativa_alumno}. Es un tropiezo frecuente: "
            "el cerebro se agarra de una palabra conocida y deja de chequear "
            "si encaja con toda la pregunta."
        )
    elif nivel == 3:
        cierre = (
            "En el examen exige nomenclatura y mecanismo exactos: descarta "
            f"la {alternativa_alumno} por inconsistencia conceptual y valida "
            f"la {alternativa_correcta} sin atajos."
        )
        intro = (
            f"Marcaste {alternativa_alumno}. Error de precisión: aplicaste "
            "un constructo vecino o incompleto donde el ítem pedía el "
            "mecanismo/estructura correcto."
        )
    else:
        cierre = (
            "Antes de marcar, elimina la opción trampa: di qué palabra del "
            f"enunciado hace falsa la {alternativa_alumno} y qué criterio "
            f"deja viva solo la {alternativa_correcta}."
        )
        intro = (
            f"Elegiste la {alternativa_alumno}. Suele ser una trampa de "
            "concepto vecino o de señal léxica del enunciado sin validar "
            "el descarte completo."
        )
    return (
        "Tu error de lógica:\n"
        f"{intro}\n\n"
        "El concepto clave:\n"
        f"La respuesta correcta es la {alternativa_correcta}. {just}\n\n"
        "Para la próxima:\n"
        f"{cierre}"
    )


def _openrouter_chat(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.35,
) -> Tuple[str, Optional[int], Optional[int]]:
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
        "model": (settings.openrouter_model or "").strip(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": _MAX_RESPUESTA_TOKENS,
    }
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=55,
        verify=ssl_verify(),
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"OpenRouter HTTP {response.status_code}: {response.text[:400]}"
        )
    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter no devolvió choices")
    content = choices[0].get("message", {}).get("content", "")
    if not content or not str(content).strip():
        raise RuntimeError("OpenRouter devolvió contenido vacío")

    usage = data.get("usage") or {}

    def _opt_int(v: Any) -> Optional[int]:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return (
        str(content).strip(),
        _opt_int(usage.get("prompt_tokens", usage.get("input_tokens"))),
        _opt_int(usage.get("completion_tokens", usage.get("output_tokens"))),
    )


def _temperature_for_nivel(nivel: int) -> float:
    """Principiante un poco más creativo (analogías); avanzado más determinista."""
    if nivel == 1:
        return 0.45
    if nivel == 3:
        return 0.25
    return 0.35


def explicar_error_alumno(
    *,
    pregunta_id: int,
    enunciado: str,
    alternativas: Dict[str, str],
    alternativa_correcta: str,
    alternativa_alumno: str,
    justificacion: str,
    tema_o_materia: str = "",
    usuario_id: Optional[int] = None,
    nivel_tutor: int = 2,
) -> ErrorCoachReply:
    """Genera coaching one-shot post-error. Soft-fail a texto local si OpenRouter cae."""
    nivel = normalize_nivel_tutor(nivel_tutor)
    correcta = str(alternativa_correcta or "").strip().upper()
    marcada = str(alternativa_alumno or "").strip().upper()
    if correcta not in _ALT_KEYS:
        raise ValueError(f"alternativa_correcta inválida: {alternativa_correcta!r}")
    if marcada not in _ALT_KEYS:
        raise ValueError(f"alternativa_alumno inválida: {alternativa_alumno!r}")
    if marcada == correcta:
        raise ValueError("No aplica coaching de error: la respuesta del alumno es correcta")

    alts = {
        k: str((alternativas or {}).get(k, "") or "").strip() for k in _ALT_KEYS
    }
    system_prompt = build_error_coach_system_prompt(nivel)
    user_msg = _build_user_payload(
        enunciado=enunciado,
        alternativas=alts,
        alternativa_correcta=correcta,
        alternativa_alumno=marcada,
        justificacion=justificacion,
        tema_o_materia=tema_o_materia,
        nivel_tutor=nivel,
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    try:
        texto, ptok, ctok = _openrouter_chat(
            messages,
            temperature=_temperature_for_nivel(nivel),
        )
        reply = ErrorCoachReply(
            texto=texto,
            model=(settings.openrouter_model or "").strip(),
            method="openrouter",
            detail=f"Coaching N{nivel} via OpenRouter",
            prompt_tokens=ptok,
            completion_tokens=ctok,
            nivel_tutor=nivel,
        )
    except Exception as exc:
        logger.warning(
            "Error coach fallback local pregunta_id=%s nivel=%s: %s: %s",
            pregunta_id,
            nivel,
            type(exc).__name__,
            exc,
        )
        reply = ErrorCoachReply(
            texto=_fallback_local(
                alternativa_alumno=marcada,
                alternativa_correcta=correcta,
                justificacion=justificacion,
                nivel_tutor=nivel,
            ),
            model=(settings.openrouter_model or "").strip(),
            method="local-fallback",
            detail=f"{type(exc).__name__}: {exc}",
            nivel_tutor=nivel,
        )

    telemetry_id: Optional[int] = None
    try:
        telemetry_id = tutor_interacciones_repository.guardar_interaccion_tutor(
            TutorInteraccionInsert(
                usuario_id=int(usuario_id) if usuario_id is not None else None,
                pregunta_id=int(pregunta_id),
                modo_origen="practica_error_coach",
                mensaje_alumno=(
                    f"[ERROR_COACH N{nivel}] eligió {marcada} "
                    f"(correcta {correcta})"
                ),
                respuesta_ia=reply.texto,
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
                model=reply.model,
                method=reply.method,
                spoilers_bloqueados=False,
            )
        )
    except Exception as exc:
        logger.debug("Telemetría error coach omitida: %s", exc)

    return ErrorCoachReply(
        texto=reply.texto,
        model=reply.model,
        method=reply.method,
        detail=reply.detail,
        prompt_tokens=reply.prompt_tokens,
        completion_tokens=reply.completion_tokens,
        telemetry_id=telemetry_id,
        nivel_tutor=nivel,
    )


def get_error_coach_reply(**kwargs: Any) -> ErrorCoachReply:
    return explicar_error_alumno(**kwargs)
