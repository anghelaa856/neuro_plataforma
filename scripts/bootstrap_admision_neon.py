"""
Bootstrap Admisión UNA → Neon.tech

Ejecuta:
  1) ensure_schema()  → usuarios + memoria_activa (intacta) + 5 tablas admisión
  2) seed Tabla 4 Biomédicas → catalogo_materias (18 materias / 60 Q / techo 3000)

Uso:
  set DATABASE_URL=postgresql://...@....neon.tech/neondb?sslmode=require
  python -m scripts.bootstrap_admision_neon

  # Solo DDL (sin re-seed explícito; el DDL ya inserta ON CONFLICT DO NOTHING):
  python -m scripts.bootstrap_admision_neon --ddl-only

  # Solo seed Python (idempotente):
  python -m scripts.bootstrap_admision_neon --seed-only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Permite ejecutar como script sin instalar el paquete
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("bootstrap_admision")


def _connect():
    from dotenv import load_dotenv

    load_dotenv(_ROOT / ".env")
    from app.database.db_manager import db_manager

    db_manager.connect()
    return db_manager


def run_ddl() -> None:
    from app.infrastructure.database.schema import ensure_schema

    logger.info("Materializando DDL en Neon (memoria_activa no se altera)...")
    ensure_schema()
    logger.info("DDL OK — dominio admisión listo.")


def run_seed(*, ensure_schema_first: bool = False) -> dict:
    from app.infrastructure.database.seed_catalogo_materias import seed_catalogo_materias

    logger.info("Sembrando catalogo_materias (Tabla 4 Biomédicas)...")
    result = seed_catalogo_materias(ensure_schema_first=ensure_schema_first)
    logger.info(
        "Seed OK — insertadas=%s ya_existentes=%s total=%s techo≈%s",
        result["insertadas"],
        result["ya_existentes"],
        result["total_activas"],
        result["techo_ponderado_teorico"],
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap DDL + seed Admisión UNA en Neon")
    parser.add_argument("--ddl-only", action="store_true", help="Solo materializa el esquema")
    parser.add_argument("--seed-only", action="store_true", help="Solo ejecuta el seed Python")
    args = parser.parse_args(argv)

    if args.ddl_only and args.seed_only:
        parser.error("Use --ddl-only o --seed-only, no ambos.")

    db_manager = _connect()
    try:
        if args.seed_only:
            result = run_seed(ensure_schema_first=True)
        elif args.ddl_only:
            run_ddl()
            result = None
        else:
            run_ddl()
            result = run_seed(ensure_schema_first=False)

        if result:
            for m in result["materias"]:
                logger.info(
                    "  [%02d] %s — %s Q × factor %s",
                    m["codigo"],
                    m["nombre_materia"],
                    m["cantidad_preguntas"],
                    m["factor_ponderacion"],
                )
        return 0
    except Exception:
        logger.exception("Bootstrap falló")
        return 1
    finally:
        db_manager.close()


if __name__ == "__main__":
    raise SystemExit(main())
