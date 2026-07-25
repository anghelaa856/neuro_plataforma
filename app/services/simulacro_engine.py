"""
Fachada de compatibilidad.

La lógica vive en `app.services.tutor_engine.TutorEngine`.
`SimulacroEngine.generar_simulacro` delega a `generar_simulacro_oficial`.
"""

from __future__ import annotations

from typing import Any, Optional

from app.services.tutor_engine import (
    AREA_DEFAULT,
    BancoInsuficienteError,
    CatalogoInvalidoError,
    PreguntaTutor as PreguntaSimulacro,
    SimulacroOficial as SimulacroGenerado,
    TutorEngine,
    tutor_engine,
)

__all__ = [
    "AREA_DEFAULT",
    "BancoInsuficienteError",
    "CatalogoInvalidoError",
    "PreguntaSimulacro",
    "SimulacroGenerado",
    "SimulacroEngine",
    "simulacro_engine",
]


class SimulacroEngine(TutorEngine):
    """Alias histórico: preferir TutorEngine en código nuevo."""

    def generar_simulacro(self, usuario_id: int, **kwargs: Any) -> SimulacroGenerado:
        return self.generar_simulacro_oficial(usuario_id, **kwargs)


simulacro_engine = SimulacroEngine(
    connection=tutor_engine._connection,
    area_examen=tutor_engine.area_examen,
)
