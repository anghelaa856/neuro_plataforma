import re
import json
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests
import certifi

from app.infrastructure.ssl_config import ssl_verify
from config.settings import settings


@dataclass
class NlpScoreResult:
    score_0_5: float
    similarity_0_1: float
    method: str
    model_name: str


@dataclass
class TutorFeedback:
    key_points: List[str]
    conceptual_errors: List[str]
    writing_suggestion: str
    method: str


@dataclass
class GeneratedQuestion:
    question: str
    reference_answer: str
    method: str
    question_type: str = "open"
    options: List[str] = field(default_factory=list)
    correct_option_index: Optional[int] = None
    source_origin: str = "pdf"


class SemanticNLPService:
    """Servicio de calificacion semantica con fallback deterministico."""

    def __init__(self, model_name: Optional[str] = None) -> None:
        self.model_name = model_name or settings.nlp_model
        self.openrouter_api_key = settings.openrouter_api_key
        self.openrouter_model = settings.openrouter_model
        self.openrouter_site = settings.openrouter_site_url
        self.openrouter_app_name = settings.openrouter_app_name
        self._model = None
        self._model_error = None

    def _load_model(self) -> None:
        if self._model is not None or self._model_error is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        except Exception as exc:
            self._model_error = str(exc)

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"\s+", " ", text)
        return text

    @staticmethod
    def _token_overlap_similarity(reference_text: str, student_text: str) -> float:
        ref_tokens = set(re.findall(r"\w+", reference_text.lower()))
        stu_tokens = set(re.findall(r"\w+", student_text.lower()))
        if not ref_tokens or not stu_tokens:
            return 0.0
        overlap = len(ref_tokens & stu_tokens)
        union = len(ref_tokens | stu_tokens)
        return max(0.0, min(1.0, overlap / union))

    def grade_answer(self, reference_text: str, student_text: str) -> NlpScoreResult:
        ref = self._normalize_text(reference_text)
        stu = self._normalize_text(student_text)

        if not ref or not stu:
            return NlpScoreResult(score_0_5=0.0, similarity_0_1=0.0, method="empty-input", model_name=self.model_name)

        self._load_model()
        if self._model is not None:
            embeddings = self._model.encode([ref, stu], normalize_embeddings=True)
            similarity = float((embeddings[0] @ embeddings[1]).item())
            similarity = max(0.0, min(1.0, similarity))
            return NlpScoreResult(
                score_0_5=round(similarity * 5.0, 2),
                similarity_0_1=round(similarity, 4),
                method="sentence-transformers",
                model_name=self.model_name,
            )

        similarity = self._token_overlap_similarity(ref, stu)
        return NlpScoreResult(
            score_0_5=round(similarity * 5.0, 2),
            similarity_0_1=round(similarity, 4),
            method="token-overlap-fallback",
            model_name=self.model_name,
        )

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
        return cleaned

    def _openrouter_chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.2, max_tokens: int = 700) -> str:
        if not self.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY no configurada.")

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.openrouter_site,
            "X-Title": self.openrouter_app_name,
        }
        payload: Dict[str, Any] = {
            "model": self.openrouter_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30,
            verify=ssl_verify(),  # truststore (CA del SO) o fallback certifi.where()
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("OpenRouter no devolvio choices")
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("OpenRouter devolvio contenido vacio")
        return content

    @staticmethod
    def _parse_mcq_payload_strict(payload: Any, n_questions: int, source_origin: str) -> List[GeneratedQuestion]:
        if isinstance(payload, dict):
            items = payload.get("questions", [])
        elif isinstance(payload, list):
            items = payload
        else:
            raise ValueError("Formato JSON invalido para preguntas MCQ")

        if not isinstance(items, list):
            raise ValueError("Campo questions debe ser una lista")

        questions: List[GeneratedQuestion] = []
        for item in items[:n_questions]:
            if not isinstance(item, dict):
                raise ValueError("Cada pregunta MCQ debe ser un objeto JSON")

            stem = str(item.get("stem", "")).strip()
            options = item.get("options", [])
            correct_option_index = item.get("correct_option_index", None)
            reference_answer = str(item.get("reference_answer", "")).strip()

            if not stem:
                raise ValueError("Falta stem en pregunta MCQ")
            if not isinstance(options, list) or len(options) < 4:
                raise ValueError("options debe incluir al menos 4 alternativas")
            clean_options = [str(opt).strip() for opt in options]
            if any(not opt for opt in clean_options):
                raise ValueError("Todas las opciones deben contener texto")
            if not isinstance(correct_option_index, int):
                raise ValueError("correct_option_index debe ser entero")
            if correct_option_index < 0 or correct_option_index >= len(clean_options):
                raise ValueError("correct_option_index fuera de rango")
            if not reference_answer:
                reference_answer = clean_options[correct_option_index]

            questions.append(
                GeneratedQuestion(
                    question=stem,
                    reference_answer=reference_answer,
                    method="openrouter",
                    question_type="mcq",
                    options=clean_options,
                    correct_option_index=correct_option_index,
                    source_origin=source_origin,
                )
            )

        if not questions:
            raise ValueError("No se generaron preguntas MCQ validas")
        return questions

    def generate_tutoring_feedback(
        self,
        question_text: str,
        reference_text: str,
        student_text: str,
    ) -> TutorFeedback:
        score = self.grade_answer(reference_text=reference_text, student_text=student_text)

        system_prompt = (
            "Eres un tutor medico-academico. Responde solo JSON valido con esta estructura exacta: "
            "{\"key_points\":[\"...\"],\"conceptual_errors\":[\"...\"],\"writing_suggestion\":\"...\"}. "
            "Maximo 3 elementos por lista, breve y accionable."
        )
        user_prompt = (
            f"Pregunta: {question_text}\n"
            f"Referencia: {reference_text}\n"
            f"Respuesta estudiante: {student_text}\n"
            "Evalua la respuesta comparandola con la referencia."
        )

        try:
            raw = self._openrouter_chat(system_prompt=system_prompt, user_prompt=user_prompt)
            payload = json.loads(self._strip_code_fences(raw))
            key_points = [str(x).strip() for x in payload.get("key_points", []) if str(x).strip()][:3]
            conceptual_errors = [str(x).strip() for x in payload.get("conceptual_errors", []) if str(x).strip()][:3]
            writing_suggestion = str(payload.get("writing_suggestion", "")).strip()

            if not key_points:
                key_points = ["Se identifican ideas parcialmente alineadas con la referencia."]
            if not writing_suggestion:
                writing_suggestion = "Usa una estructura: definicion, mecanismo y efecto clinico/contable." 

            return TutorFeedback(
                key_points=key_points,
                conceptual_errors=conceptual_errors,
                writing_suggestion=writing_suggestion,
                method="openrouter",
            )
        except Exception:
            # Fallback local en caso de error de red, API key o formato invalido.
            key_points: List[str] = []
            conceptual_errors: List[str] = []

            if score.score_0_5 >= 4.0:
                key_points.append("La respuesta cubre correctamente la idea central de la referencia.")
            elif score.score_0_5 >= 2.5:
                key_points.append("La respuesta contiene elementos correctos pero incompletos.")
                conceptual_errors.append("Faltan detalles clave para justificar completamente el concepto.")
            else:
                conceptual_errors.append("La respuesta se aleja del concepto principal planteado.")

            writing_suggestion = (
                "Redacta en 3 pasos: 1) definicion precisa, 2) mecanismo o proceso, "
                "3) impacto o aplicacion concreta."
            )

            return TutorFeedback(
                key_points=key_points or ["Se detecta un intento inicial de respuesta."],
                conceptual_errors=conceptual_errors,
                writing_suggestion=writing_suggestion,
                method="local-fallback",
            )

    def generate_questions_from_text(
        self,
        content_text: str,
        n_questions: int = 3,
        question_mode: str = "open",
        source_origin: str = "pdf",
    ) -> List[GeneratedQuestion]:
        cleaned = re.sub(r"\s+", " ", content_text).strip()
        if not cleaned:
            return []

        if question_mode == "mcq":
            system_prompt = (
                "Genera preguntas tipo opcion multiple. Responde solo JSON valido con esta estructura exacta: "
                "{\"questions\":[{\"stem\":\"...\",\"options\":[\"...\",\"...\",\"...\",\"...\"],"
                "\"correct_option_index\":0,\"reference_answer\":\"...\"}]}. "
                "No incluyas texto extra fuera del JSON."
            )
            user_prompt = (
                f"Genera {n_questions} casos clinicos/tecnicos de opcion multiple basados en el contenido. "
                f"Contenido: {cleaned[:7000]}"
            )
        else:
            system_prompt = (
                "Genera preguntas de evocacion activa (respuesta abierta). "
                "Responde solo JSON valido como lista de objetos con campos question y reference_answer."
            )
            user_prompt = (
                f"A partir del contenido, genera {n_questions} preguntas abiertas aleatorias y una respuesta de referencia breve para cada una. "
                f"Contenido: {cleaned[:7000]}"
            )

        try:
            raw = self._openrouter_chat(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.6, max_tokens=800)
            payload = json.loads(self._strip_code_fences(raw))
            if question_mode == "mcq":
                return self._parse_mcq_payload_strict(payload=payload, n_questions=n_questions, source_origin=source_origin)

            if isinstance(payload, dict):
                items = payload.get("questions", [])
            else:
                items = payload

            results: List[GeneratedQuestion] = []
            for item in items[:n_questions]:
                if not isinstance(item, dict):
                    continue
                question = str(item.get("question", "")).strip()
                ref = str(item.get("reference_answer", "")).strip()
                if question and ref:
                    results.append(
                        GeneratedQuestion(
                            question=question,
                            reference_answer=ref,
                            method="openrouter",
                            question_type="open",
                            source_origin=source_origin,
                        )
                    )

            if results:
                return results
        except Exception:
            pass

        # Fallback local: muestreo de frases relevantes.
        sentences = [
            s.strip()
            for s in re.split(r"(?<=[.!?])\s+", cleaned)
            if len(s.strip()) >= 40
        ]
        if not sentences:
            sentences = [cleaned]

        sample = random.sample(sentences, k=min(n_questions, len(sentences)))
        questions: List[GeneratedQuestion] = []
        if question_mode == "mcq":
            for idx, sentence in enumerate(sample, start=1):
                distractors = [
                    "Solo aplica en casos excepcionales.",
                    "Depende unicamente del azar.",
                    "No tiene impacto en la toma de decisiones.",
                ]
                correct = sentence[:160]
                options = [correct] + distractors
                random.shuffle(options)
                correct_idx = options.index(correct)
                questions.append(
                    GeneratedQuestion(
                        question=f"Caso {idx}: ¿Cuál afirmación describe mejor el concepto central del contenido?",
                        reference_answer=correct,
                        method="local-fallback",
                        question_type="mcq",
                        options=options,
                        correct_option_index=correct_idx,
                        source_origin=source_origin,
                    )
                )
            return questions

        for idx, sentence in enumerate(sample, start=1):
            questions.append(
                GeneratedQuestion(
                    question=f"Pregunta {idx}: Explica con tus palabras la idea principal del siguiente fragmento.",
                    reference_answer=sentence[:500],
                    method="local-fallback",
                    question_type="open",
                    source_origin=source_origin,
                )
            )
        return questions
