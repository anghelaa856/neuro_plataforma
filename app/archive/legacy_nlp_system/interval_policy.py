from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass
class IntervalDecision:
    interval_days: int
    quality: int
    repetitions: int
    easiness: float
    strategy: str


class IntervalPolicyService:
    """Politica hibrida para sugerir intervalos usando SM-2 y señal tipo DQN."""

    PLAN_INTENSIVO = "Plan Intensivo (Repaso de 1 día)"
    PLAN_RETENCION = "Plan Retención a Largo Plazo (SM-2/Anki)"

    ACTION_TO_DAYS: Dict[int, int] = {0: 1, 1: 3, 2: 7, 3: 15}

    @staticmethod
    def quality_from_signals(score_0_5: float, is_anomaly: bool) -> int:
        # Mapeo deterministico 0-5 para SM-2.
        quality = int(round(max(0.0, min(5.0, score_0_5))))
        if is_anomaly:
            quality = max(0, quality - 1)
        return quality

    @staticmethod
    def _sm2_next(quality: int, repetitions: int, easiness: float, interval_days: int) -> Tuple[int, int, float]:
        repetitions = max(0, repetitions)
        easiness = max(1.3, easiness)
        interval_days = max(1, interval_days)

        if quality < 3:
            next_repetitions = 0
            next_interval = 1
        else:
            next_repetitions = repetitions + 1
            if next_repetitions == 1:
                next_interval = 1
            elif next_repetitions == 2:
                next_interval = 6
            else:
                next_interval = int(round(interval_days * easiness))

        ef_prime = easiness + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        next_easiness = max(1.3, ef_prime)
        return next_interval, next_repetitions, round(next_easiness, 3)

    def _dqn_like_suggestion(self, score_0_5: float, is_anomaly: bool, interval_days: int) -> int:
        # Politica deterministica equivalente a una capa lineal para 4 acciones.
        base_score = max(0.0, min(5.0, score_0_5)) / 5.0
        anomaly_penalty = 0.35 if is_anomaly else 0.0
        interval_factor = min(1.0, interval_days / 30.0)

        q_values = [
            0.9 - base_score + anomaly_penalty,
            0.7 + 0.2 * base_score + 0.1 * anomaly_penalty,
            0.45 + 0.65 * base_score - 0.2 * anomaly_penalty,
            0.2 + 0.9 * base_score + 0.3 * interval_factor - anomaly_penalty,
        ]
        action = int(max(range(len(q_values)), key=lambda idx: q_values[idx]))
        return self.ACTION_TO_DAYS[action]

    def recommend_interval(
        self,
        score_0_5: float,
        is_anomaly: bool,
        repetitions: int,
        easiness: float,
        interval_days: int,
    ) -> IntervalDecision:
        quality = self.quality_from_signals(score_0_5=score_0_5, is_anomaly=is_anomaly)
        sm2_interval, next_reps, next_ef = self._sm2_next(
            quality=quality,
            repetitions=repetitions,
            easiness=easiness,
            interval_days=interval_days,
        )
        dqn_interval = self._dqn_like_suggestion(
            score_0_5=score_0_5,
            is_anomaly=is_anomaly,
            interval_days=interval_days,
        )

        # Fusion robusta: prioriza SM-2 en bajo desempeno y mezcla en alto desempeno.
        if quality < 3:
            final_interval = sm2_interval
            strategy = "sm2-recovery"
        else:
            final_interval = int(round((sm2_interval * 0.7) + (dqn_interval * 0.3)))
            strategy = "hybrid-sm2-dqn"

        return IntervalDecision(
            interval_days=max(1, final_interval),
            quality=quality,
            repetitions=next_reps,
            easiness=next_ef,
            strategy=strategy,
        )

    def apply_study_plan(self, interval_days: int, study_plan: str) -> int:
        """Aplica reglas del plan de estudio sobre el intervalo recomendado."""
        if study_plan == self.PLAN_INTENSIVO:
            return 1
        return max(1, int(interval_days))

    @staticmethod
    def decide_question_mode(
        is_new_topic: bool,
        interval_days: int,
        is_mock_mode: bool,
    ) -> str:
        """
        Reglas Nivel Dios:
        - Tema nuevo o intervalo corto (<21): evocacion activa (open)
        - Tema maduro (>21) o simulacro: opcion multiple (mcq)
        """
        if is_mock_mode:
            return "mcq"
        if is_new_topic:
            return "open"
        if interval_days > 21:
            return "mcq"
        return "open"
