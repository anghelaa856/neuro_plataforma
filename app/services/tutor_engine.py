"""
TutorEngine — doble motor de preparación para Admisión UNA (Biomédicas).

1) generar_simulacro_oficial  → examen 60 Q con muestreo estratificado (prospecto).
2) generar_practica_enfocada  → bloque libre (SRS + tasa de error), sin cupos oficiales.

Interacción con historial_intentos
---------------------------------
- Ambos motores SOLO LEEN el ledger para priorizar (nunca hacen UPDATE).
- Simulacro oficial: agrega tasa de error por tema/pregunta (sesiones + práctica).
- Práctica enfocada: usa el último intento por pregunta para estimar vencimiento
  (intervalo tipo SM-2 ligero) y la tasa de error histórica para reforzar fallos.
- Al responder después:
    · Simulacro  → INSERT con sesion_id = id de sesiones_simulacro
    · Práctica   → INSERT con sesion_id = NULL  (práctica libre)
"""

from __future__ import annotations

import logging
import random
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from app.infrastructure.database.connection import DatabaseConnection, db_connection

logger = logging.getLogger(__name__)

AREA_DEFAULT = "BIOMEDICAS"
TOTAL_OFICIAL = 60
PUNTOS_CORRECTA = 10

# Intervalos (días) tipo SM-2 ligero según racha de aciertos consecutivos al final.
_INTERVALOS_RACHA = {0: 1, 1: 1, 2: 3, 3: 7}  # 4+ → 15


class BancoInsuficienteError(RuntimeError):
    """No hay suficientes ítems activos para el cupo solicitado."""


class CatalogoInvalidoError(RuntimeError):
    """El catálogo oficial no suma 60 o está incompleto."""


@dataclass
class PreguntaTutor:
    orden: int
    id_pregunta: int
    materia_id: int
    materia_codigo: int
    materia_nombre: str
    factor_ponderacion: float
    tema_id: int
    tema_nombre: str
    enunciado: str
    alternativas: Dict[str, Any]
    alternativa_correcta: str
    peso_prioridad: float
    motivo_prioridad: str = ""
    cantidad_cupo_materia: Optional[int] = None

    def to_public_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.pop("alternativa_correcta", None)
        return data


@dataclass
class SimulacroOficial:
    id_sesion: int
    usuario_id: int
    area_examen: str
    seed_muestreo: int
    total_preguntas: int
    puntaje_maximo_ponderado: float
    modo: str = "simulacro_oficial"
    preguntas: List[PreguntaTutor] = field(default_factory=list)
    composicion_por_materia: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self, *, incluir_clave: bool = False) -> Dict[str, Any]:
        return {
            "modo": self.modo,
            "id_sesion": self.id_sesion,
            "usuario_id": self.usuario_id,
            "area_examen": self.area_examen,
            "seed_muestreo": self.seed_muestreo,
            "total_preguntas": self.total_preguntas,
            "puntaje_maximo_ponderado": self.puntaje_maximo_ponderado,
            "composicion_por_materia": self.composicion_por_materia,
            "preguntas": [
                asdict(p) if incluir_clave else p.to_public_dict() for p in self.preguntas
            ],
            # Contrato con historial_intentos al registrar respuestas:
            "sesion_id_para_intentos": self.id_sesion,
        }


@dataclass
class PracticaEnfocada:
    usuario_id: int
    materia_id: Optional[int]
    tema_id: Optional[int]
    limite: int
    total_preguntas: int
    modo: str = "practica_enfocada"
    preguntas: List[PreguntaTutor] = field(default_factory=list)
    resumen_prioridad: Dict[str, int] = field(default_factory=dict)

    def to_dict(self, *, incluir_clave: bool = False) -> Dict[str, Any]:
        return {
            "modo": self.modo,
            "usuario_id": self.usuario_id,
            "materia_id": self.materia_id,
            "tema_id": self.tema_id,
            "limite": self.limite,
            "total_preguntas": self.total_preguntas,
            "resumen_prioridad": self.resumen_prioridad,
            "preguntas": [
                asdict(p) if incluir_clave else p.to_public_dict() for p in self.preguntas
            ],
            # Práctica libre: ledger con sesion_id NULL
            "sesion_id_para_intentos": None,
        }


class TutorEngine:
    """
    Núcleo pedagógico: simulacro oficial (estratificado) + práctica libre (SRS).
    """

    def __init__(
        self,
        connection: Optional[DatabaseConnection] = None,
        *,
        area_examen: str = AREA_DEFAULT,
        weak_tema_boost: float = 3.0,
        weak_pregunta_boost: float = 2.0,
        min_intentos_tema: int = 2,
    ) -> None:
        self._connection = connection or db_connection
        self.area_examen = area_examen
        self.weak_tema_boost = weak_tema_boost
        self.weak_pregunta_boost = weak_pregunta_boost
        self.min_intentos_tema = min_intentos_tema

    # ================================================================== API
    def generar_simulacro_oficial(
        self,
        usuario_id: int,
        *,
        seed: Optional[int] = None,
        persistir_sesion: bool = True,
    ) -> SimulacroOficial:
        """
        Examen de 60 preguntas exactas.
        JOIN catalogo_materias → cupo por materia; prioriza fallos históricos
        del usuario (historial_intentos) dentro de cada estrato.
        """
        uid = int(usuario_id)
        seed_muestreo = int(
            seed if seed is not None else random.SystemRandom().randint(1, 2**31 - 1)
        )
        rng = np.random.default_rng(seed_muestreo)

        catalogo = self._load_catalogo()
        self._assert_catalogo_60(catalogo)
        banco = self._load_banco_activo()
        if banco.empty:
            raise BancoInsuficienteError(
                "banco_preguntas vacío. Cargue ítems antes del simulacro oficial."
            )

        deb_temas, deb_preguntas = self._load_debilidades_agregadas(uid)
        seleccionadas: List[PreguntaTutor] = []
        composicion: List[Dict[str, Any]] = []
        orden = 1

        for _, materia in catalogo.iterrows():
            cupo = int(materia["cantidad_preguntas"])
            pool = banco[banco["materia_id"] == int(materia["id_materia"])].copy()
            if len(pool) < cupo:
                raise BancoInsuficienteError(
                    f"Materia '{materia['nombre']}' requiere {cupo}; banco tiene {len(pool)}."
                )

            sample = self._sample_estratificado_materia(
                pool=pool,
                cupo=cupo,
                rng=rng,
                debilidad_temas=deb_temas,
                debilidad_preguntas=deb_preguntas,
            )

            for _, row in sample.iterrows():
                seleccionadas.append(
                    self._row_to_pregunta(
                        row=row,
                        orden=orden,
                        materia=materia,
                        cupo=cupo,
                        peso=float(row["peso_muestreo"]),
                        motivo="estrato_oficial+debilidad",
                    )
                )
                orden += 1

            composicion.append(
                {
                    "materia_id": int(materia["id_materia"]),
                    "codigo": int(materia["codigo"]),
                    "nombre": str(materia["nombre"]),
                    "cupo": cupo,
                    "factor_ponderacion": float(materia["factor_ponderacion"]),
                    "aporte_maximo_ponderado": round(
                        PUNTOS_CORRECTA * cupo * float(materia["factor_ponderacion"]), 3
                    ),
                    "temas_seleccionados": sorted(
                        {str(t) for t in sample["tema_nombre"].tolist()}
                    ),
                }
            )

        if len(seleccionadas) != TOTAL_OFICIAL:
            raise RuntimeError(
                f"Invariante roto: {len(seleccionadas)} preguntas ≠ {TOTAL_OFICIAL}."
            )

        order_idx = list(range(len(seleccionadas)))
        rng.shuffle(order_idx)
        mezcladas = [seleccionadas[i] for i in order_idx]
        for i, preg in enumerate(mezcladas, start=1):
            preg.orden = i

        techo = self.calcular_techo_ponderado(catalogo)
        id_sesion = 0
        if persistir_sesion:
            id_sesion = self._abrir_sesion(
                usuario_id=uid,
                seed_muestreo=seed_muestreo,
                puntaje_maximo=techo,
            )

        logger.info(
            "Simulacro oficial usuario=%s sesion=%s seed=%s techo=%.3f",
            uid,
            id_sesion,
            seed_muestreo,
            techo,
        )
        return SimulacroOficial(
            id_sesion=id_sesion,
            usuario_id=uid,
            area_examen=self.area_examen,
            seed_muestreo=seed_muestreo,
            total_preguntas=TOTAL_OFICIAL,
            puntaje_maximo_ponderado=techo,
            preguntas=mezcladas,
            composicion_por_materia=composicion,
        )

    def generar_practica_enfocada(
        self,
        usuario_id: int,
        materia_id: Optional[int] = None,
        tema_id: Optional[int] = None,
        limite: int = 15,
        *,
        seed: Optional[int] = None,
    ) -> PracticaEnfocada:
        """
        Bloque de estudio libre (sin cupos del prospecto).

        Prioriza con historial_intentos:
          1) intervalos vencidos (SRS ligero desde último intento),
          2) alta tasa de error,
          3) nunca vistas (exploración).
        """
        uid = int(usuario_id)
        lim = max(1, int(limite))
        if materia_id is None and tema_id is None:
            raise ValueError("Indique materia_id y/o tema_id para la práctica enfocada.")

        rng = np.random.default_rng(
            int(seed if seed is not None else random.SystemRandom().randint(1, 2**31 - 1))
        )

        banco = self._load_banco_activo(materia_id=materia_id, tema_id=tema_id)
        if banco.empty:
            raise BancoInsuficienteError(
                "No hay preguntas activas para el filtro materia/tema solicitado."
            )

        stats = self._load_stats_srs_por_pregunta(uid, banco["id_pregunta"].tolist())
        ranked = self._rank_practica_srs(banco=banco, stats=stats, hoy=date.today(), rng=rng)

        top = ranked.head(min(lim, len(ranked)))
        preguntas: List[PreguntaTutor] = []
        resumen = {"vencidas": 0, "alta_error": 0, "nunca_vistas": 0, "refuerzo": 0}

        for orden, (_, row) in enumerate(top.iterrows(), start=1):
            motivo = str(row["motivo_prioridad"])
            resumen[motivo] = resumen.get(motivo, 0) + 1
            preguntas.append(
                PreguntaTutor(
                    orden=orden,
                    id_pregunta=int(row["id_pregunta"]),
                    materia_id=int(row["materia_id"]),
                    materia_codigo=int(row["materia_codigo"]),
                    materia_nombre=str(row["materia_nombre"]),
                    factor_ponderacion=float(row["factor_ponderacion"]),
                    tema_id=int(row["tema_id"]),
                    tema_nombre=str(row["tema_nombre"]),
                    enunciado=str(row["enunciado"]),
                    alternativas=dict(row["alternativas"])
                    if isinstance(row["alternativas"], dict)
                    else row["alternativas"],
                    alternativa_correcta=str(row["alternativa_correcta"]),
                    peso_prioridad=float(row["peso_prioridad"]),
                    motivo_prioridad=motivo,
                    cantidad_cupo_materia=None,
                )
            )

        logger.info(
            "Práctica enfocada usuario=%s materia=%s tema=%s n=%s resumen=%s",
            uid,
            materia_id,
            tema_id,
            len(preguntas),
            resumen,
        )
        return PracticaEnfocada(
            usuario_id=uid,
            materia_id=int(materia_id) if materia_id is not None else None,
            tema_id=int(tema_id) if tema_id is not None else None,
            limite=lim,
            total_preguntas=len(preguntas),
            preguntas=preguntas,
            resumen_prioridad=resumen,
        )

    # Alias explícito pedido por el plan de ejecución
    def generar_simulacro(self, usuario_id: int, **kwargs: Any) -> SimulacroOficial:
        return self.generar_simulacro_oficial(usuario_id, **kwargs)

    # ============================================================== Scoring
    @staticmethod
    def calcular_techo_ponderado(catalogo: pd.DataFrame) -> float:
        total = (
            PUNTOS_CORRECTA
            * catalogo["cantidad_preguntas"].astype(float)
            * catalogo["factor_ponderacion"].astype(float)
        ).sum()
        return float(round(total, 3))

    @staticmethod
    def puntaje_ponderado_item(puntos_base: int, factor_ponderacion: float) -> float:
        return float(round(int(puntos_base) * float(factor_ponderacion), 3))

    # ============================================================== Loads
    def _load_catalogo(self) -> pd.DataFrame:
        query = """
        SELECT id_materia, codigo, nombre, cantidad_preguntas, factor_ponderacion
        FROM catalogo_materias
        WHERE area_examen = %s AND activo
        ORDER BY codigo;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, (self.area_examen,))
            rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            raise CatalogoInvalidoError(
                f"catalogo_materias vacío para {self.area_examen}. Ejecute el seed."
            )
        return pd.DataFrame(rows)

    def _load_banco_activo(
        self,
        *,
        materia_id: Optional[int] = None,
        tema_id: Optional[int] = None,
    ) -> pd.DataFrame:
        clauses = ["p.activa", "m.area_examen = %s", "m.activo", "t.activo"]
        params: List[Any] = [self.area_examen]
        if materia_id is not None:
            clauses.append("t.materia_id = %s")
            params.append(int(materia_id))
        if tema_id is not None:
            clauses.append("t.id_tema = %s")
            params.append(int(tema_id))

        query = f"""
        SELECT
            p.id_pregunta,
            p.tema_id,
            t.nombre AS tema_nombre,
            t.materia_id,
            m.codigo AS materia_codigo,
            m.nombre AS materia_nombre,
            m.cantidad_preguntas,
            m.factor_ponderacion,
            p.enunciado,
            p.alternativas,
            p.alternativa_correcta
        FROM banco_preguntas p
        JOIN temas_estudio t ON t.id_tema = p.tema_id
        JOIN catalogo_materias m ON m.id_materia = t.materia_id
        WHERE {" AND ".join(clauses)};
        """
        with self._connection.get_cursor() as cur:
            cur.execute(query, tuple(params))
            rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return pd.DataFrame(
                columns=[
                    "id_pregunta",
                    "tema_id",
                    "tema_nombre",
                    "materia_id",
                    "materia_codigo",
                    "materia_nombre",
                    "cantidad_preguntas",
                    "factor_ponderacion",
                    "enunciado",
                    "alternativas",
                    "alternativa_correcta",
                ]
            )
        return pd.DataFrame(rows)

    def _load_debilidades_agregadas(
        self,
        usuario_id: int,
    ) -> tuple[Dict[int, float], Dict[int, float]]:
        """Tasas de error desde historial_intentos (simulacro + práctica libre)."""
        q_temas = """
        SELECT
            t.id_tema,
            COUNT(*) FILTER (WHERE h.alternativa_marcada IS NOT NULL) AS n_marcadas,
            COUNT(*) FILTER (WHERE h.es_correcta IS FALSE) AS n_incorrectas
        FROM historial_intentos h
        JOIN banco_preguntas p ON p.id_pregunta = h.pregunta_id
        JOIN temas_estudio t ON t.id_tema = p.tema_id
        WHERE h.usuario_id = %s
        GROUP BY t.id_tema;
        """
        q_preguntas = """
        SELECT
            h.pregunta_id,
            COUNT(*) FILTER (WHERE h.alternativa_marcada IS NOT NULL) AS n_marcadas,
            COUNT(*) FILTER (WHERE h.es_correcta IS FALSE) AS n_incorrectas
        FROM historial_intentos h
        WHERE h.usuario_id = %s
        GROUP BY h.pregunta_id;
        """
        deb_temas: Dict[int, float] = {}
        deb_preguntas: Dict[int, float] = {}
        with self._connection.get_cursor() as cur:
            cur.execute(q_temas, (usuario_id,))
            for row in cur.fetchall():
                n = int(row["n_marcadas"] or 0)
                if n < self.min_intentos_tema:
                    continue
                deb_temas[int(row["id_tema"])] = int(row["n_incorrectas"] or 0) / n

            cur.execute(q_preguntas, (usuario_id,))
            for row in cur.fetchall():
                n = int(row["n_marcadas"] or 0)
                if n < 1:
                    continue
                deb_preguntas[int(row["pregunta_id"])] = int(row["n_incorrectas"] or 0) / n
        return deb_temas, deb_preguntas

    def _load_stats_srs_por_pregunta(
        self,
        usuario_id: int,
        pregunta_ids: Sequence[int],
    ) -> pd.DataFrame:
        """
        Agrega historial_intentos por pregunta para SRS:
        último intento, tasa de error, racha final de aciertos.
        """
        if not pregunta_ids:
            return pd.DataFrame(
                columns=[
                    "pregunta_id",
                    "n_intentos",
                    "tasa_error",
                    "ultimo_en",
                    "ultimo_correcto",
                    "racha_aciertos",
                ]
            )

        query = """
        WITH base AS (
            SELECT
                h.pregunta_id,
                h.fecha_hora,
                h.es_correcta,
                h.alternativa_marcada,
                ROW_NUMBER() OVER (
                    PARTITION BY h.pregunta_id ORDER BY h.fecha_hora DESC
                ) AS rn_desc
            FROM historial_intentos h
            WHERE h.usuario_id = %s
              AND h.pregunta_id = ANY(%s)
        ),
        agg AS (
            SELECT
                pregunta_id,
                COUNT(*) AS n_intentos,
                COUNT(*) FILTER (WHERE alternativa_marcada IS NOT NULL) AS n_marcadas,
                COUNT(*) FILTER (WHERE es_correcta IS FALSE) AS n_incorrectas
            FROM historial_intentos
            WHERE usuario_id = %s
              AND pregunta_id = ANY(%s)
            GROUP BY pregunta_id
        ),
        ultimo AS (
            SELECT
                pregunta_id,
                fecha_hora AS ultimo_en,
                es_correcta AS ultimo_correcto
            FROM base
            WHERE rn_desc = 1
        ),
        rachas AS (
            SELECT
                pregunta_id,
                COUNT(*) AS racha_aciertos
            FROM (
                SELECT
                    pregunta_id,
                    es_correcta,
                    ROW_NUMBER() OVER (
                        PARTITION BY pregunta_id ORDER BY fecha_hora DESC
                    ) AS rn,
                    SUM(CASE WHEN es_correcta IS TRUE THEN 0 ELSE 1 END) OVER (
                        PARTITION BY pregunta_id ORDER BY fecha_hora DESC
                        ROWS UNBOUNDED PRECEDING
                    ) AS fallos_acum
                FROM historial_intentos
                WHERE usuario_id = %s
                  AND pregunta_id = ANY(%s)
                  AND alternativa_marcada IS NOT NULL
            ) s
            WHERE fallos_acum = 0 AND es_correcta IS TRUE
            GROUP BY pregunta_id
        )
        SELECT
            a.pregunta_id,
            a.n_intentos,
            CASE
                WHEN a.n_marcadas = 0 THEN NULL
                ELSE ROUND(a.n_incorrectas::NUMERIC / a.n_marcadas, 4)
            END AS tasa_error,
            u.ultimo_en,
            u.ultimo_correcto,
            COALESCE(r.racha_aciertos, 0) AS racha_aciertos
        FROM agg a
        LEFT JOIN ultimo u ON u.pregunta_id = a.pregunta_id
        LEFT JOIN rachas r ON r.pregunta_id = a.pregunta_id;
        """
        ids = [int(x) for x in pregunta_ids]
        with self._connection.get_cursor() as cur:
            cur.execute(query, (usuario_id, ids, usuario_id, ids, usuario_id, ids))
            rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return pd.DataFrame(
                columns=[
                    "pregunta_id",
                    "n_intentos",
                    "tasa_error",
                    "ultimo_en",
                    "ultimo_correcto",
                    "racha_aciertos",
                ]
            )
        return pd.DataFrame(rows)

    # ============================================================== Ranking
    def _sample_estratificado_materia(
        self,
        *,
        pool: pd.DataFrame,
        cupo: int,
        rng: np.random.Generator,
        debilidad_temas: Dict[int, float],
        debilidad_preguntas: Dict[int, float],
    ) -> pd.DataFrame:
        weights: List[float] = []
        for _, row in pool.iterrows():
            w_tema = float(debilidad_temas.get(int(row["tema_id"]), 0.0))
            w_preg = float(debilidad_preguntas.get(int(row["id_pregunta"]), 0.0))
            peso = 1.0 + self.weak_tema_boost * w_tema + self.weak_pregunta_boost * w_preg
            weights.append(max(peso, 1e-9))

        weights_arr = np.asarray(weights, dtype=float)
        probs = weights_arr / weights_arr.sum()
        indices = rng.choice(len(pool), size=cupo, replace=False, p=probs)
        sample = pool.iloc[list(indices)].copy()
        sample["peso_muestreo"] = weights_arr[list(indices)]
        return sample.reset_index(drop=True)

    def _rank_practica_srs(
        self,
        *,
        banco: pd.DataFrame,
        stats: pd.DataFrame,
        hoy: date,
        rng: np.random.Generator,
    ) -> pd.DataFrame:
        merged = banco.merge(
            stats,
            how="left",
            left_on="id_pregunta",
            right_on="pregunta_id",
        )
        pesos: List[float] = []
        motivos: List[str] = []

        for _, row in merged.iterrows():
            jitter = float(rng.uniform(0.0, 1.0))
            n_intentos = int(row["n_intentos"]) if pd.notna(row.get("n_intentos")) else 0
            tasa_error = float(row["tasa_error"]) if pd.notna(row.get("tasa_error")) else 0.0
            racha = int(row["racha_aciertos"]) if pd.notna(row.get("racha_aciertos")) else 0
            ultimo_en = row.get("ultimo_en")
            ultimo_ok = row.get("ultimo_correcto")

            if n_intentos == 0 or pd.isna(ultimo_en):
                # Exploración: nunca vista
                pesos.append(70.0 + 20.0 * jitter)
                motivos.append("nunca_vistas")
                continue

            ultimo_dia = self._as_date(ultimo_en)
            intervalo = self._intervalo_srs(
                ultimo_correcto=bool(ultimo_ok) if pd.notna(ultimo_ok) else False,
                racha_aciertos=racha,
                tasa_error=tasa_error,
            )
            dias_desde = (hoy - ultimo_dia).days
            dias_vencido = dias_desde - intervalo

            if dias_vencido >= 0:
                # Vencida: máxima prioridad + error histórico
                pesos.append(100.0 + 8.0 * dias_vencido + 40.0 * tasa_error + jitter)
                motivos.append("vencidas")
            elif tasa_error >= 0.5:
                pesos.append(85.0 + 30.0 * tasa_error + jitter)
                motivos.append("alta_error")
            else:
                # Refuerzo leve / aún no vencida
                pesos.append(20.0 + 25.0 * tasa_error + jitter)
                motivos.append("refuerzo")

        out = merged.copy()
        out["peso_prioridad"] = pesos
        out["motivo_prioridad"] = motivos
        return out.sort_values("peso_prioridad", ascending=False).reset_index(drop=True)

    @staticmethod
    def _intervalo_srs(
        *,
        ultimo_correcto: bool,
        racha_aciertos: int,
        tasa_error: float,
    ) -> int:
        if not ultimo_correcto:
            return 1  # fallo / en blanco → repaso inmediato (1 día)
        if tasa_error >= 0.6:
            return 1
        if racha_aciertos >= 4:
            return 15
        return _INTERVALOS_RACHA.get(racha_aciertos, 1)

    @staticmethod
    def _as_date(value: Any) -> date:
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                return value.astimezone(timezone.utc).date()
            return value.date()
        if isinstance(value, date):
            return value
        return pd.Timestamp(value).date()

    def _row_to_pregunta(
        self,
        *,
        row: pd.Series,
        orden: int,
        materia: pd.Series,
        cupo: int,
        peso: float,
        motivo: str,
    ) -> PreguntaTutor:
        return PreguntaTutor(
            orden=orden,
            id_pregunta=int(row["id_pregunta"]),
            materia_id=int(materia["id_materia"]),
            materia_codigo=int(materia["codigo"]),
            materia_nombre=str(materia["nombre"]),
            factor_ponderacion=float(materia["factor_ponderacion"]),
            tema_id=int(row["tema_id"]),
            tema_nombre=str(row["tema_nombre"]),
            enunciado=str(row["enunciado"]),
            alternativas=dict(row["alternativas"])
            if isinstance(row["alternativas"], dict)
            else row["alternativas"],
            alternativa_correcta=str(row["alternativa_correcta"]),
            peso_prioridad=peso,
            motivo_prioridad=motivo,
            cantidad_cupo_materia=cupo,
        )

    @staticmethod
    def _assert_catalogo_60(catalogo: pd.DataFrame) -> None:
        total = int(catalogo["cantidad_preguntas"].sum())
        if total != TOTAL_OFICIAL:
            raise CatalogoInvalidoError(
                f"Σ cantidad_preguntas={total}; debe ser {TOTAL_OFICIAL}."
            )
        if len(catalogo) != 18:
            logger.warning(
                "Catálogo con %s materias (oficial Biomédicas=18). Σ=%s",
                len(catalogo),
                total,
            )

    def _abrir_sesion(
        self,
        *,
        usuario_id: int,
        seed_muestreo: int,
        puntaje_maximo: float,
    ) -> int:
        query = """
        INSERT INTO sesiones_simulacro (
            usuario_id, area_examen, total_preguntas, tiempo_maximo_minutos,
            puntos_correcta, puntos_en_blanco, puntos_incorrecta,
            puntaje_maximo_ponderado, estado, seed_muestreo
        )
        VALUES (%s, %s, 60, 120, 10, 2, 0, %s, 'en_curso', %s)
        RETURNING id_sesion;
        """
        with self._connection.get_cursor() as cur:
            cur.execute(
                query,
                (usuario_id, self.area_examen, puntaje_maximo, seed_muestreo),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("No se pudo abrir sesiones_simulacro.")
            return int(row["id_sesion"])


tutor_engine = TutorEngine()
