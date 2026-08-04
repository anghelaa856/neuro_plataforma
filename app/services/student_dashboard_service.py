"""
Analytics ligeros del alumno: Índice Medicina, plan semanal y semáforo sin rojo.

No escribe en historial_intentos ni altera scoring.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

# Precisión sostenida = Meta Medicina (aprobado por producto).
META_MEDICINA_MIN = 85.0
META_MEDICINA_IDEAL = 90.0
BANDA_COMPETITIVA_MIN = 70.0
# Muestra mínima para no presentar el índice como “verdad absoluta”.
MUESTRA_CONFIABLE = 15
# Umbral mínimo de intentos para confiar en el Nivel de Dominio por materia.
DOMINIO_MUESTRA_MIN = 5


def nivel_dominio(
    precision_pct: float,
    n_intentos: int,
    *,
    muestra_min: int = DOMINIO_MUESTRA_MIN,
) -> Dict[str, Any]:
    """
    KPI de dominio evolutivo por materia (0–100 + banda pedagógica).

    - novato / explorar: poca evidencia o baja precisión → MCQ básicas
    - en_progreso: precisión media → intermedias + aplicación
    - competente: ≥ banda competitiva → casos clínicos
    - dominio: ≥ meta Medicina → análisis profundo / multi-paso
    """
    n = max(0, int(n_intentos or 0))
    p = float(precision_pct or 0.0)
    if n < max(1, int(muestra_min)):
        # Evidencia insuficiente: score suave hacia novato/exploración.
        score = round(min(35.0, p * 0.35 + n * 2.0), 1)
        banda = "novato"
        target = "basica"
        hint = (
            "Poca evidencia de dominio: genera ítems claros de recuerdo y "
            "comprensión literal del material."
        )
    elif p < BANDA_COMPETITIVA_MIN:
        score = round(p * 0.75, 1)
        banda = "en_progreso"
        target = "intermedia"
        hint = (
            "Dominio parcial: prioriza aplicación directa, discriminación de "
            "conceptos cercanos y trampas típicas de admisión."
        )
    elif p < META_MEDICINA_MIN:
        score = round(55.0 + (p - BANDA_COMPETITIVA_MIN) * 1.2, 1)
        banda = "competente"
        target = "avanzada"
        hint = (
            "Alto % de aciertos: elabora casos clínicos cortos, integración "
            "fisiopatológica y alternativas técnicamente plausibles."
        )
    else:
        score = round(min(100.0, 80.0 + (p - META_MEDICINA_MIN) * 1.5), 1)
        banda = "dominio"
        target = "avanzada"
        hint = (
            "Dominio alto: exige análisis profundo, razonamiento multi-paso, "
            "excepciones clínicas y discriminación experta entre distractores."
        )

    return {
        "score": float(score),
        "banda": banda,
        "target_nivel": target,
        "precision_pct": round(p, 1),
        "n_intentos": n,
        "hint_llm": hint,
        "etiqueta": f"{banda.replace('_', ' ').title()} ({score:.0f})",
    }


def enriquecer_resumen_con_dominio(
    resumen_materias: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Añade Nivel de Dominio a cada fila de ``resumen_por_materia``."""
    out: List[Dict[str, Any]] = []
    for row in resumen_materias:
        item = dict(row)
        dom = nivel_dominio(
            float(item.get("precision_pct") or 0.0),
            int(item.get("n_intentos") or 0),
        )
        item["dominio_score"] = dom["score"]
        item["dominio_banda"] = dom["banda"]
        item["dominio_target"] = dom["target_nivel"]
        item["dominio_etiqueta"] = dom["etiqueta"]
        item["dominio_hint"] = dom["hint_llm"]
        out.append(item)
    return out


def format_dominio_para_llm(
    dominio_por_materia: Sequence[Dict[str, Any]],
    *,
    max_rows: int = 18,
) -> str:
    """Bloque de contexto para el prompt de extracción adaptativa."""
    rows = [dict(r) for r in (dominio_por_materia or []) if r.get("materia_nombre")]
    if not rows:
        return (
            "PERFIL DE DOMINIO DEL ESTUDIANTE: sin historial suficiente. "
            "Calibra nivel_estimado en 'basica'/'intermedia' (exploración)."
        )

    # Priorizar materias con más evidencia / mayor dominio para calibrar complejidad.
    rows.sort(
        key=lambda r: (
            -float(r.get("dominio_score") or 0.0),
            -int(r.get("n_intentos") or 0),
        )
    )
    lines: List[str] = []
    for r in rows[: max(1, int(max_rows))]:
        nombre = str(r.get("materia_nombre") or "")
        banda = str(r.get("dominio_banda") or "novato")
        score = float(r.get("dominio_score") or 0.0)
        target = str(r.get("dominio_target") or "basica")
        prec = float(r.get("precision_pct") or 0.0)
        n = int(r.get("n_intentos") or 0)
        hint = str(r.get("dominio_hint") or "")
        lines.append(
            f"- {nombre}: dominio={banda} ({score:.0f}/100), "
            f"precisión={prec:.0f}% en {n} intentos → "
            f"preferir nivel_estimado='{target}'. {hint}"
        )

    return (
        "PERFIL DE DOMINIO DEL ESTUDIANTE (calibra la complejidad por materia):\n"
        + "\n".join(lines)
        + "\n"
        "REGLA ADAPTATIVA: si el fragmento cae en una materia con banda "
        "'competente' o 'dominio', las MCQ deben ser más elaboradas "
        "(casos clínicos, análisis, integración). Si es 'novato'/'en_progreso', "
        "mantén ítems claros y de comprensión directa. "
        "Asigna nivel_estimado acorde a esa calibración."
    )


def recomendar_temas_urgentes(
    rendimiento_por_tema: Sequence[Dict[str, Any]],
    *,
    min_intentos: int = 3,
    top_n: int = 3,
) -> List[Dict[str, Any]]:
    """
    Ordena temas de menor a mayor precisión y devuelve los más urgentes.

    Solo considera temas con al menos ``min_intentos`` intentos
    (validez estadística mínima).
    """
    candidatos: List[Dict[str, Any]] = []
    for row in rendimiento_por_tema:
        n = int(row.get("n_intentos") or 0)
        if n < int(min_intentos):
            continue
        precis = float(row.get("precision_pct") or 0.0)
        item = dict(row)
        item["precision_pct"] = precis
        item["mensaje"] = (
            f"Tienes un {precis:.0f}% de precisión en el tema "
            f"«{row.get('tema_nombre', 'Tema')}» de "
            f"«{row.get('materia_nombre', 'Materia')}» "
            f"({n} intentos). Te recomendamos enfocar tu próxima práctica aquí."
        )
        candidatos.append(item)

    candidatos.sort(
        key=lambda r: (
            float(r.get("precision_pct") or 0.0),
            -int(r.get("n_intentos") or 0),
        )
    )
    return candidatos[: max(0, int(top_n))]


def etiqueta_trayectoria(precision_pct: float) -> str:
    """Priorizar / Mejorar / Fuerte — sin rojo castigador."""
    p = float(precision_pct)
    if p < 55.0:
        return "Priorizar"
    if p < META_MEDICINA_MIN:
        return "Mejorar"
    return "Fuerte"


def banda_indice(indice: float) -> str:
    """Base | Competitiva | Meta Medicina."""
    v = float(indice)
    if v < BANDA_COMPETITIVA_MIN:
        return "Base"
    if v < META_MEDICINA_MIN:
        return "Competitiva"
    return "Meta Medicina"


def calcular_indice_medicina(
    *,
    precision_pct: float,
    total_intentos: int,
    precision_7d: Optional[float] = None,
    precision_7d_prev: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Índice Medicina = precisión sostenida (0–100).

    La Meta Medicina se ancla en 85–90% de precisión, no en puntaje UNA.
    """
    indice = round(max(0.0, min(100.0, float(precision_pct))), 1)
    banda = banda_indice(indice)
    muestra_temprana = int(total_intentos) < MUESTRA_CONFIABLE

    delta: Optional[float] = None
    if precision_7d is not None and precision_7d_prev is not None:
        delta = round(float(precision_7d) - float(precision_7d_prev), 1)

    if banda == "Meta Medicina":
        frase = (
            "Estás en zona Meta Medicina. Consolida con repasos cortos "
            "para sostener el 85–90%."
        )
        estado = "Excelente trayectoria hacia Medicina"
    elif banda == "Competitiva":
        gap = round(META_MEDICINA_MIN - indice, 1)
        frase = (
            f"Zona competitiva. Te faltan ~{gap:.0f} pts de precisión sostenida "
            f"para entrar en Meta Medicina ({META_MEDICINA_MIN:.0f}%)."
        )
        estado = "Camino sólido hacia Medicina"
    else:
        frase = (
            "Aún estás construyendo base. Eso es normal: el plan de abajo "
            "cierra la brecha más rápido."
        )
        estado = "Construyendo base hacia Medicina"

    if muestra_temprana and int(total_intentos) > 0:
        frase = "Estimación temprana · " + frase

    return {
        "indice": indice,
        "banda": banda,
        "estado": estado,
        "frase_pronostico": frase,
        "muestra_temprana": muestra_temprana,
        "delta_semanal": delta,
        "meta_min": META_MEDICINA_MIN,
        "meta_ideal": META_MEDICINA_IDEAL,
        "competitiva_min": BANDA_COMPETITIVA_MIN,
    }


def resumen_por_materia(
    rendimiento_por_tema: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Agrega precisión ponderada por intentos a nivel materia."""
    buckets: Dict[int, Dict[str, Any]] = {}
    for row in rendimiento_por_tema:
        mid = int(row["id_materia"])
        n = int(row.get("n_intentos") or 0)
        correctas = int(row.get("correctas") or 0)
        incorrectas = int(row.get("incorrectas") or 0)
        if mid not in buckets:
            buckets[mid] = {
                "id_materia": mid,
                "materia_codigo": int(row.get("materia_codigo") or 0),
                "materia_nombre": str(row.get("materia_nombre") or ""),
                "n_intentos": 0,
                "correctas": 0,
                "incorrectas": 0,
            }
        buckets[mid]["n_intentos"] += n
        buckets[mid]["correctas"] += correctas
        buckets[mid]["incorrectas"] += incorrectas

    out: List[Dict[str, Any]] = []
    for b in buckets.values():
        decididos = int(b["correctas"]) + int(b["incorrectas"])
        precision = (
            round(100.0 * int(b["correctas"]) / decididos, 1) if decididos > 0 else 0.0
        )
        etiqueta = etiqueta_trayectoria(precision)
        out.append(
            {
                **b,
                "precision_pct": precision,
                "etiqueta": etiqueta,
            }
        )

    # Priorizar primero, luego peor precisión.
    orden_etiq = {"Priorizar": 0, "Mejorar": 1, "Fuerte": 2}
    out.sort(
        key=lambda r: (
            orden_etiq.get(str(r["etiqueta"]), 9),
            float(r["precision_pct"]),
        )
    )
    return out


def construir_plan_semanal(
    rendimiento_por_tema: Sequence[Dict[str, Any]],
    *,
    min_intentos: int = 3,
) -> Dict[str, Any]:
    """
    Tres slots fijos: cuello de botella, refuerzo rápido, mantener ritmo.
    """
    urgentes = recomendar_temas_urgentes(
        rendimiento_por_tema, min_intentos=min_intentos, top_n=5
    )
    if not urgentes:
        urgentes = recomendar_temas_urgentes(
            rendimiento_por_tema, min_intentos=1, top_n=5
        )

    fuertes = [
        dict(r, precision_pct=float(r.get("precision_pct") or 0.0))
        for r in rendimiento_por_tema
        if int(r.get("n_intentos") or 0) >= max(1, int(min_intentos))
        and float(r.get("precision_pct") or 0.0) >= BANDA_COMPETITIVA_MIN
    ]
    fuertes.sort(
        key=lambda r: (
            -float(r.get("precision_pct") or 0.0),
            -int(r.get("n_intentos") or 0),
        )
    )

    cuello = urgentes[0] if urgentes else None
    refuerzo = None
    for cand in urgentes[1:]:
        if etiqueta_trayectoria(float(cand.get("precision_pct") or 0)) != "Fuerte":
            refuerzo = cand
            break
    if refuerzo is None and len(urgentes) > 1:
        refuerzo = urgentes[1]

    usados = {
        int(x["id_tema"])
        for x in (cuello, refuerzo)
        if x is not None and x.get("id_tema") is not None
    }
    mantener = None
    for f in fuertes:
        tid = int(f["id_tema"]) if f.get("id_tema") is not None else None
        if tid is not None and tid in usados:
            continue
        mantener = f
        break

    mision = None
    if cuello is not None:
        p0 = float(cuello.get("precision_pct") or 0.0)
        meta_parcial = min(META_MEDICINA_MIN, round(p0 + 15.0, 0))
        if meta_parcial <= p0:
            meta_parcial = min(META_MEDICINA_IDEAL, p0 + 5.0)
        mision = {
            "tema_nombre": str(cuello.get("tema_nombre") or "tema"),
            "materia_nombre": str(cuello.get("materia_nombre") or ""),
            "desde_pct": p0,
            "hasta_pct": float(meta_parcial),
            "texto": (
                f"Misión de la semana: subir «{cuello.get('tema_nombre')}» "
                f"de {p0:.0f}% a {meta_parcial:.0f}%."
            ),
        }

    return {
        "cuello_botella": cuello,
        "refuerzo": refuerzo,
        "mantener": mantener,
        "mision": mision,
        "vacio": cuello is None,
    }
