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
from app.infrastructure.database.historial_repository import (
    CierreSesionSimulacro,
    HistorialRepository,
    IntentoLedger,
    historial_repository,
)

logger = logging.getLogger(__name__)

AREA_DEFAULT = "BIOMEDICAS"
TOTAL_OFICIAL = 60
PUNTOS_CORRECTA = 10
PUNTOS_EN_BLANCO = 2
PUNTOS_INCORRECTA = 0

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


@dataclass
class ResultadoCierreBloque:
    """Resumen tras persistir respuestas en historial_intentos."""

    modo: str
    n_insertados: int
    correctas: int
    incorrectas: int
    en_blanco: int
    puntaje_bruto: float
    puntaje_ponderado: float
    tiempo_total_ms: int
    ids_intento: List[int] = field(default_factory=list)
    id_sesion: Optional[int] = None
    detalle_por_pregunta: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
        historial: Optional[HistorialRepository] = None,
    ) -> None:
        self._connection = connection or db_connection
        self._historial = historial or historial_repository
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
        fuente_banco: str = "todo",
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
        banco = self._load_banco_activo(usuario_id=uid, fuente_banco=fuente_banco)
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
        fuente_banco: str = "todo",
    ) -> PracticaEnfocada:
        """
        Bloque de estudio libre (sin cupos del prospecto).

        Si ``materia_id`` y ``tema_id`` son None → Modo Global (mezcla todo el banco).

        Prioriza con historial_intentos:
          1) intervalos vencidos (SRS ligero desde último intento),
          2) alta tasa de error,
          3) nunca vistas (exploración).
        """
        uid = int(usuario_id)
        lim = max(1, int(limite))
        # materia_id/tema_id opcionales: ambos None = Modo Global (todo el banco).
        mid = int(materia_id) if materia_id is not None else None
        tid = int(tema_id) if tema_id is not None else None
        if mid is not None and mid <= 0:
            mid = None
        if tid is not None and tid <= 0:
            tid = None

        rng = np.random.default_rng(
            int(seed if seed is not None else random.SystemRandom().randint(1, 2**31 - 1))
        )

        banco = self._load_banco_activo(
            materia_id=mid,
            tema_id=tid,
            usuario_id=uid,
            fuente_banco=fuente_banco,
        )
        if banco.empty:
            raise BancoInsuficienteError(
                "No hay preguntas activas para el filtro materia/tema/fuente solicitado."
            )

        stats = self._load_stats_srs_por_pregunta(uid, banco["id_pregunta"].tolist())
        ranked = self._rank_practica_srs(banco=banco, stats=stats, hoy=date.today(), rng=rng)

        top = ranked.head(min(lim, len(ranked)))
        # Modo Global: remueve clustering temático del ranking SRS.
        if mid is None and tid is None and len(top) > 1:
            order = list(range(len(top)))
            rng.shuffle(order)
            top = top.iloc[order].reset_index(drop=True)

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
            mid,
            tid,
            len(preguntas),
            resumen,
        )
        return PracticaEnfocada(
            usuario_id=uid,
            materia_id=mid,
            tema_id=tid,
            limite=lim,
            total_preguntas=len(preguntas),
            preguntas=preguntas,
            resumen_prioridad=resumen,
        )

    def generar_practica_debilidades(
        self,
        usuario_id: int,
        *,
        limite: int = 12,
        top_temas: int = 5,
        min_intentos: int = 2,
        fuente_banco: str = "todo",
        seed: Optional[int] = None,
    ) -> PracticaEnfocada:
        """
        Práctica inteligente: mezcla preguntas de los temas con peor precisión
        (misma lógica que alimenta el dashboard de rendimiento).
        """
        from app.services.student_dashboard_service import recomendar_temas_urgentes

        uid = int(usuario_id)
        lim = max(10, min(15, int(limite)))
        rendimiento = self._historial.fetch_rendimiento_por_tema(uid)
        urgentes = recomendar_temas_urgentes(
            rendimiento,
            min_intentos=int(min_intentos),
            top_n=max(1, int(top_temas)),
        )
        if not urgentes:
            # Sin muestra estadística: usa los de menor precisión aunque tengan 1 intento.
            urgentes = recomendar_temas_urgentes(
                rendimiento, min_intentos=1, top_n=max(1, int(top_temas))
            )
        if not urgentes:
            raise BancoInsuficienteError(
                "Aún no hay historial suficiente para detectar debilidades. "
                "Haz primero una práctica o simulacro y vuelve a intentarlo."
            )

        tema_ids = [int(t["id_tema"]) for t in urgentes if t.get("id_tema") is not None]
        rng = np.random.default_rng(
            int(seed if seed is not None else random.SystemRandom().randint(1, 2**31 - 1))
        )
        banco = self._load_banco_activo(
            tema_ids=tema_ids,
            usuario_id=uid,
            fuente_banco=fuente_banco,
        )
        if banco.empty:
            raise BancoInsuficienteError(
                "Hay temas débiles, pero no quedan preguntas activas en esas áreas "
                "para la fuente elegida."
            )

        stats = self._load_stats_srs_por_pregunta(uid, banco["id_pregunta"].tolist())
        ranked = self._rank_practica_srs(banco=banco, stats=stats, hoy=date.today(), rng=rng)
        top = ranked.head(min(lim, len(ranked)))
        preguntas: List[PreguntaTutor] = []
        resumen = {
            "vencidas": 0,
            "alta_error": 0,
            "nunca_vistas": 0,
            "refuerzo": 0,
            "temas_debiles": len(tema_ids),
        }
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
            "Práctica debilidades usuario=%s temas=%s n=%s resumen=%s",
            uid,
            tema_ids,
            len(preguntas),
            resumen,
        )
        return PracticaEnfocada(
            usuario_id=uid,
            materia_id=None,
            tema_id=None,
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

    # ======================================================== Cierre / ledger
    @staticmethod
    def _normalizar_marcada(raw: Any) -> Optional[str]:
        if raw is None:
            return None
        text = str(raw).strip().upper()
        if not text or text not in {"A", "B", "C", "D", "E"}:
            return None
        return text

    @classmethod
    def evaluar_alternativa(
        cls,
        *,
        alternativa_marcada: Optional[str],
        alternativa_correcta: str,
        factor_ponderacion: float,
    ) -> Dict[str, Any]:
        """
        Reglas UNA: 10 correcta, 2 en blanco, 0 incorrecta.
        En blanco ⇒ es_correcta NULL (coherente con CHECK del ledger).
        """
        marcada = cls._normalizar_marcada(alternativa_marcada)
        correcta = str(alternativa_correcta or "").strip().upper()
        factor = float(factor_ponderacion)

        if marcada is None:
            puntos = PUNTOS_EN_BLANCO
            return {
                "alternativa_marcada": None,
                "es_correcta": None,
                "puntaje_obtenido": puntos,
                "puntaje_ponderado": cls.puntaje_ponderado_item(puntos, factor),
            }

        es_ok = marcada == correcta
        puntos = PUNTOS_CORRECTA if es_ok else PUNTOS_INCORRECTA
        return {
            "alternativa_marcada": marcada,
            "es_correcta": es_ok,
            "puntaje_obtenido": puntos,
            "puntaje_ponderado": cls.puntaje_ponderado_item(puntos, factor),
        }

    def _construir_intentos(
        self,
        *,
        usuario_id: int,
        preguntas: Sequence[PreguntaTutor],
        respuestas: Dict[str, Any],
        tiempos_ms: Dict[str, Any],
        sesion_id: Optional[int],
        incluir_orden: bool,
    ) -> tuple[List[IntentoLedger], ResultadoCierreBloque]:
        intentos: List[IntentoLedger] = []
        detalle: List[Dict[str, Any]] = []
        correctas = incorrectas = en_blanco = 0
        puntaje_bruto = 0.0
        puntaje_ponderado = 0.0
        tiempo_total = 0

        for preg in preguntas:
            qid = int(preg.id_pregunta)
            key = str(qid)
            marcada_raw = respuestas.get(key, respuestas.get(qid))
            eval_item = self.evaluar_alternativa(
                alternativa_marcada=marcada_raw,
                alternativa_correcta=preg.alternativa_correcta,
                factor_ponderacion=preg.factor_ponderacion,
            )
            raw_t = tiempos_ms.get(key, tiempos_ms.get(qid, 0))
            try:
                t_ms = max(0, int(raw_t or 0))
            except (TypeError, ValueError):
                t_ms = 0

            if eval_item["es_correcta"] is True:
                correctas += 1
            elif eval_item["es_correcta"] is False:
                incorrectas += 1
            else:
                en_blanco += 1

            puntaje_bruto += float(eval_item["puntaje_obtenido"])
            puntaje_ponderado += float(eval_item["puntaje_ponderado"])
            tiempo_total += t_ms

            orden = int(preg.orden) if incluir_orden else None
            intentos.append(
                IntentoLedger(
                    usuario_id=int(usuario_id),
                    pregunta_id=qid,
                    sesion_id=int(sesion_id) if sesion_id is not None else None,
                    orden_en_sesion=orden,
                    tiempo_respuesta_ms=t_ms,
                    alternativa_marcada=eval_item["alternativa_marcada"],
                    es_correcta=eval_item["es_correcta"],
                    puntaje_obtenido=int(eval_item["puntaje_obtenido"]),
                    factor_ponderacion_aplicado=float(preg.factor_ponderacion),
                    puntaje_ponderado=float(eval_item["puntaje_ponderado"]),
                )
            )
            detalle.append(
                {
                    "orden": preg.orden,
                    "id_pregunta": qid,
                    "materia_nombre": preg.materia_nombre,
                    "tema_nombre": preg.tema_nombre,
                    "alternativa_marcada": eval_item["alternativa_marcada"],
                    "alternativa_correcta": preg.alternativa_correcta,
                    "es_correcta": eval_item["es_correcta"],
                    "puntaje_obtenido": eval_item["puntaje_obtenido"],
                    "puntaje_ponderado": eval_item["puntaje_ponderado"],
                    "tiempo_respuesta_ms": t_ms,
                }
            )

        resumen = ResultadoCierreBloque(
            modo="",
            n_insertados=0,
            correctas=correctas,
            incorrectas=incorrectas,
            en_blanco=en_blanco,
            puntaje_bruto=float(round(puntaje_bruto, 3)),
            puntaje_ponderado=float(round(puntaje_ponderado, 3)),
            tiempo_total_ms=tiempo_total,
            id_sesion=int(sesion_id) if sesion_id is not None else None,
            detalle_por_pregunta=detalle,
        )
        return intentos, resumen

    def registrar_intento_practica(
        self,
        *,
        usuario_id: int,
        pregunta_id: int,
        alternativa_marcada: Optional[str],
        alternativa_correcta: str,
        factor_ponderacion: float,
        orden_en_sesion: Optional[int] = None,
        tiempo_respuesta_ms: int = 0,
        alternativa_marcada_banco: Optional[str] = None,
    ) -> int:
        """
        INSERT incremental (1 fila) en historial_intentos con sesion_id=NULL.

        Usado por Reflex en Práctica Enfocada tras cada ``check_answer``.
        ``alternativa_marcada`` se evalúa vs ``alternativa_correcta`` (letras
        ya remapeadas en UI). En el ledger se guarda preferentemente la letra
        del banco (``alternativa_marcada_banco``) para auditoría.
        """
        eval_item = self.evaluar_alternativa(
            alternativa_marcada=alternativa_marcada,
            alternativa_correcta=alternativa_correcta,
            factor_ponderacion=factor_ponderacion,
        )
        marcada_ledger = alternativa_marcada_banco
        if marcada_ledger is None:
            marcada_ledger = eval_item["alternativa_marcada"]
        elif marcada_ledger is not None:
            marcada_ledger = self._normalizar_marcada(marcada_ledger)

        intento = IntentoLedger(
            usuario_id=int(usuario_id),
            pregunta_id=int(pregunta_id),
            sesion_id=None,
            orden_en_sesion=int(orden_en_sesion) if orden_en_sesion is not None else None,
            tiempo_respuesta_ms=max(0, int(tiempo_respuesta_ms or 0)),
            alternativa_marcada=marcada_ledger,
            es_correcta=eval_item["es_correcta"],
            puntaje_obtenido=int(eval_item["puntaje_obtenido"]),
            factor_ponderacion_aplicado=float(factor_ponderacion),
            puntaje_ponderado=float(eval_item["puntaje_ponderado"]),
        )
        ids = self._historial.insert_intentos([intento])
        id_intento = int(ids[0]) if ids else 0
        logger.info(
            "Intento práctica usuario=%s pregunta=%s ok=%s id_intento=%s",
            usuario_id,
            pregunta_id,
            eval_item["es_correcta"],
            id_intento,
        )
        return id_intento

    def finalizar_practica_enfocada(
        self,
        *,
        practica: PracticaEnfocada,
        respuestas: Dict[str, Any],
        tiempos_ms: Optional[Dict[str, Any]] = None,
    ) -> ResultadoCierreBloque:
        """
        Evalúa el bloque libre y hace INSERT en historial_intentos
        con sesion_id = NULL (SRS / práctica libre).
        """
        if not practica.preguntas:
            raise ValueError("La práctica no tiene preguntas para finalizar.")

        intentos, resumen = self._construir_intentos(
            usuario_id=practica.usuario_id,
            preguntas=practica.preguntas,
            respuestas=respuestas or {},
            tiempos_ms=tiempos_ms or {},
            sesion_id=None,
            incluir_orden=False,
        )
        ids = self._historial.insert_intentos(intentos)
        resumen.modo = "practica_enfocada"
        resumen.n_insertados = len(ids)
        resumen.ids_intento = ids
        logger.info(
            "Práctica finalizada usuario=%s n=%s correctas=%s tiempo_ms=%s",
            practica.usuario_id,
            len(ids),
            resumen.correctas,
            resumen.tiempo_total_ms,
        )
        return resumen

    def finalizar_simulacro_oficial(
        self,
        *,
        simulacro: SimulacroOficial,
        respuestas: Dict[str, Any],
        tiempos_ms: Optional[Dict[str, Any]] = None,
    ) -> ResultadoCierreBloque:
        """
        INSERT ledger (con sesion_id) + UPDATE sesiones_simulacro → finalizada.
        """
        if not simulacro.preguntas:
            raise ValueError("El simulacro no tiene preguntas para finalizar.")
        if int(simulacro.id_sesion) <= 0:
            raise ValueError(
                "Simulacro sin id_sesion persistido; no se puede cerrar en BD."
            )

        intentos, resumen = self._construir_intentos(
            usuario_id=simulacro.usuario_id,
            preguntas=simulacro.preguntas,
            respuestas=respuestas or {},
            tiempos_ms=tiempos_ms or {},
            sesion_id=int(simulacro.id_sesion),
            incluir_orden=True,
        )
        cierre = CierreSesionSimulacro(
            respuestas_correctas=resumen.correctas,
            respuestas_incorrectas=resumen.incorrectas,
            respuestas_en_blanco=resumen.en_blanco,
            puntaje_bruto=resumen.puntaje_bruto,
            puntaje_ponderado=resumen.puntaje_ponderado,
            tiempo_total_ms=resumen.tiempo_total_ms,
        )
        persistido = self._historial.persistir_cierre_simulacro(
            intentos=intentos,
            id_sesion=int(simulacro.id_sesion),
            usuario_id=int(simulacro.usuario_id),
            cierre=cierre,
        )
        resumen.modo = "simulacro_oficial"
        resumen.n_insertados = int(persistido["n_insertados"])
        resumen.ids_intento = list(persistido["ids_intento"])
        logger.info(
            "Simulacro finalizado sesion=%s usuario=%s ponderado=%.3f tiempo_ms=%s",
            simulacro.id_sesion,
            simulacro.usuario_id,
            resumen.puntaje_ponderado,
            resumen.tiempo_total_ms,
        )
        return resumen

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
        tema_ids: Optional[Sequence[int]] = None,
        usuario_id: Optional[int] = None,
        fuente_banco: str = "todo",
    ) -> pd.DataFrame:
        """
        Carga ítems activos con aislamiento Fase 7.

        fuente_banco:
          - oficial → solo propietario_usuario_id IS NULL
          - mis_guias → solo propietario_usuario_id = usuario_id
          - todo → oficial OR del alumno (nunca de otros)
        """
        clauses = ["p.activa", "m.area_examen = %s", "m.activo", "t.activo"]
        params: List[Any] = [self.area_examen]
        if materia_id is not None:
            clauses.append("t.materia_id = %s")
            params.append(int(materia_id))
        if tema_id is not None:
            clauses.append("t.id_tema = %s")
            params.append(int(tema_id))
        if tema_ids:
            ids = sorted({int(x) for x in tema_ids if x is not None})
            if ids:
                placeholders = ", ".join(["%s"] * len(ids))
                clauses.append(f"t.id_tema IN ({placeholders})")
                params.extend(ids)

        scope = (fuente_banco or "todo").strip().lower()
        if scope == "oficial":
            clauses.append("p.propietario_usuario_id IS NULL")
        elif scope == "mis_guias":
            if usuario_id is None:
                raise ValueError("fuente_banco=mis_guias requiere usuario_id.")
            clauses.append("p.propietario_usuario_id = %s")
            params.append(int(usuario_id))
        else:
            # todo: oficial + propias (aislamiento: nunca ver guías de otros)
            if usuario_id is None:
                clauses.append("p.propietario_usuario_id IS NULL")
            else:
                clauses.append(
                    "(p.propietario_usuario_id IS NULL OR p.propietario_usuario_id = %s)"
                )
                params.append(int(usuario_id))

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
            p.alternativa_correcta,
            p.propietario_usuario_id
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
                    "propietario_usuario_id",
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
