"""Bootstrap de Neon y acceso lazy a repositorios/servicios Streamlit-domain."""

from __future__ import annotations

from typing import Any, Optional

_bootstrapped = False
_boot_error: str = ""


def get_boot_error() -> str:
    return _boot_error


def is_db_ready() -> bool:
    return _bootstrapped and not _boot_error


def bootstrap_database() -> bool:
    """Conecta pool Neon + schema. Idempotente."""
    global _bootstrapped, _boot_error
    if _bootstrapped and not _boot_error:
        return True
    try:
        from app.database.db_manager import db_manager
        from app.infrastructure.database.schema import ensure_schema
        from app.infrastructure.database.seed_catalogo_materias import (
            seed_catalogo_materias,
        )

        db_manager.connect(minconn=1, maxconn=3)
        ensure_schema()
        try:
            seed_catalogo_materias()
        except Exception:
            pass
        _bootstrapped = True
        _boot_error = ""
        return True
    except Exception as exc:
        _boot_error = str(exc)
        _bootstrapped = False
        return False


def user_repo():
    from app.infrastructure.database.user_repository import user_repository

    return user_repository


def banco_repo():
    from app.infrastructure.database.banco_repository import banco_repository

    return banco_repository


def historial_repo():
    from app.infrastructure.database.historial_repository import historial_repository

    return historial_repository


def engine():
    from app.services.tutor_engine import tutor_engine

    return tutor_engine


def pregunta_to_public(p: Any) -> dict[str, Any]:
    """Serializa PreguntaTutor sin filtrar clave (uso solo server-side / _vars)."""
    alts = getattr(p, "alternativas", {}) or {}
    if not isinstance(alts, dict):
        alts = {}
    return {
        "id_pregunta": int(getattr(p, "id_pregunta", 0) or 0),
        "orden": int(getattr(p, "orden", 0) or 0),
        "materia_nombre": str(getattr(p, "materia_nombre", "") or ""),
        "tema_nombre": str(getattr(p, "tema_nombre", "") or ""),
        "enunciado": str(getattr(p, "enunciado", "") or ""),
        "alternativas": {k: str(alts.get(k, "")) for k in ("A", "B", "C", "D", "E")},
        "alternativa_correcta": str(
            getattr(p, "alternativa_correcta", "") or ""
        ).upper(),
        "justificacion": "",
        "factor_ponderacion": float(getattr(p, "factor_ponderacion", 1.0) or 1.0),
    }


def hydrate_justificaciones(preguntas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not is_db_ready():
        return preguntas
    try:
        repo = banco_repo()
        out = []
        for q in preguntas:
            item = dict(q)
            if not item.get("justificacion"):
                just = repo.fetch_justificacion(int(item["id_pregunta"]))
                item["justificacion"] = just or "Revisa el enunciado y las opciones."
            out.append(item)
        return out
    except Exception:
        return preguntas
