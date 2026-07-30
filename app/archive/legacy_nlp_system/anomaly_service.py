from collections import deque
from dataclasses import dataclass
from typing import Deque, Iterable, Optional

import numpy as np

from config.settings import settings


@dataclass
class AnomalyResult:
    is_anomaly: bool
    state: str
    score: float
    method: str


class TimeAnomalyService:
    """Detector de anomalias de tiempo de respuesta con degradacion segura."""

    def __init__(
        self,
        seed_times_ms: Optional[Iterable[int]] = None,
        contamination: Optional[float] = None,
        min_ms: int = 800,
        max_ms: int = 40000,
        history_size: int = 256,
    ) -> None:
        base_times = list(seed_times_ms or [8100, 7500, 8900, 7800, 8200, 7900, 8500, 7700, 8300, 8000])
        self._history: Deque[int] = deque(base_times, maxlen=history_size)
        contamination_value = (
            contamination if contamination is not None else settings.anomaly_contamination
        )
        self.contamination = max(0.001, min(0.5, contamination_value))
        self.min_ms = min_ms
        self.max_ms = max_ms
        self._model = None
        self._model_error = None

    def _fit_model(self) -> None:
        if self._model is not None or self._model_error is not None:
            return
        try:
            from sklearn.ensemble import IsolationForest

            x = np.array(list(self._history), dtype=np.float32).reshape(-1, 1)
            model = IsolationForest(contamination=self.contamination, random_state=42, n_estimators=150)
            model.fit(x)
            self._model = model
        except Exception as exc:
            self._model_error = str(exc)

    def evaluate_response_time(self, response_time_ms: int) -> AnomalyResult:
        response_time_ms = int(max(0, response_time_ms))

        self._fit_model()
        if self._model is not None:
            x = np.array([[response_time_ms]], dtype=np.float32)
            pred = int(self._model.predict(x)[0])
            decision = float(self._model.decision_function(x)[0])
            is_anomaly = pred == -1
            self._history.append(response_time_ms)
            return AnomalyResult(
                is_anomaly=is_anomaly,
                state="Anomalia" if is_anomaly else "Normal",
                score=round(decision, 4),
                method="isolation-forest",
            )

        is_anomaly = response_time_ms < self.min_ms or response_time_ms > self.max_ms
        self._history.append(response_time_ms)
        return AnomalyResult(
            is_anomaly=is_anomaly,
            state="Anomalia" if is_anomaly else "Normal",
            score=0.0,
            method="threshold-fallback",
        )
