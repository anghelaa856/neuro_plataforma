"""Banco local de preguntas — esquema alineado a `banco_preguntas` (Streamlit/Neon).

Nota: en producción el contenido vive en Neon. Este archivo replica el shape
de `PreguntaTutor` / `ItemBancoGenerado` para desarrollar sin DB.
"""

from __future__ import annotations

from typing import TypedDict

# Factor oficial materia 7 · Biología y Anatomía (seed_catalogo_materias.py)
FACTOR_BIOLOGIA_ANATOMIA = 7.816


class Question(TypedDict):
    id_pregunta: int
    materia_nombre: str
    tema_nombre: str
    enunciado: str
    alternativas: dict[str, str]
    alternativa_correcta: str
    justificacion: str
    factor_ponderacion: float


QUESTIONS: list[Question] = [
    {
        "id_pregunta": 1,
        "materia_nombre": "Biología y Anatomía",
        "tema_nombre": "Sistema nervioso",
        "enunciado": (
            "¿Cuál de las siguientes estructuras forma parte "
            "del sistema nervioso central?"
        ),
        "alternativas": {
            "A": "Nervio ciático",
            "B": "Médula espinal",
            "C": "Nervio vago",
            "D": "Plexo braquial",
            "E": "Ganglio simpático cervical",
        },
        "alternativa_correcta": "B",
        "justificacion": (
            "El SNC está formado por el encéfalo y la médula espinal. "
            "Nervios, plexos y ganglios pertenecen al sistema nervioso periférico."
        ),
        "factor_ponderacion": FACTOR_BIOLOGIA_ANATOMIA,
    },
    {
        "id_pregunta": 2,
        "materia_nombre": "Biología y Anatomía",
        "tema_nombre": "Sistema cardiovascular",
        "enunciado": (
            "¿Cuál es la cavidad cardíaca que recibe sangre oxigenada "
            "proveniente de los pulmones?"
        ),
        "alternativas": {
            "A": "Aurícula derecha",
            "B": "Ventrículo derecho",
            "C": "Aurícula izquierda",
            "D": "Vena cava superior",
            "E": "Seno coronario",
        },
        "alternativa_correcta": "C",
        "justificacion": (
            "Las venas pulmonares llevan sangre oxigenada a la aurícula izquierda; "
            "desde allí pasa al ventrículo izquierdo hacia la circulación sistémica."
        ),
        "factor_ponderacion": FACTOR_BIOLOGIA_ANATOMIA,
    },
    {
        "id_pregunta": 3,
        "materia_nombre": "Biología y Anatomía",
        "tema_nombre": "Osteología",
        "enunciado": "¿Cuál es el hueso más largo del cuerpo humano?",
        "alternativas": {
            "A": "Húmero",
            "B": "Tibia",
            "C": "Fíbula",
            "D": "Fémur",
            "E": "Radio",
        },
        "alternativa_correcta": "D",
        "justificacion": (
            "El fémur es el hueso más largo y resistente; articula la pelvis "
            "con la tibia en la rodilla."
        ),
        "factor_ponderacion": FACTOR_BIOLOGIA_ANATOMIA,
    },
    {
        "id_pregunta": 4,
        "materia_nombre": "Biología y Anatomía",
        "tema_nombre": "Sistema respiratorio",
        "enunciado": (
            "¿En qué estructura ocurre principalmente el intercambio "
            "gaseoso o hematosis?"
        ),
        "alternativas": {
            "A": "Tráquea",
            "B": "Bronquios principales",
            "C": "Alvéolos pulmonares",
            "D": "Laringe",
            "E": "Bronquiolos terminales",
        },
        "alternativa_correcta": "C",
        "justificacion": (
            "Los alvéolos maximizan la superficie de contacto aire-sangre "
            "para el intercambio de O₂ y CO₂."
        ),
        "factor_ponderacion": FACTOR_BIOLOGIA_ANATOMIA,
    },
    {
        "id_pregunta": 5,
        "materia_nombre": "Biología y Anatomía",
        "tema_nombre": "Sistema digestivo",
        "enunciado": (
            "¿En cuál de estos órganos se produce principalmente la bilis?"
        ),
        "alternativas": {
            "A": "Páncreas",
            "B": "Bazo",
            "C": "Hígado",
            "D": "Vesícula biliar",
            "E": "Duodeno",
        },
        "alternativa_correcta": "C",
        "justificacion": (
            "La bilis la sintetiza el hígado. La vesícula solo la almacena "
            "y concentra; el páncreas secreta enzimas y bicarbonato."
        ),
        "factor_ponderacion": FACTOR_BIOLOGIA_ANATOMIA,
    },
    {
        "id_pregunta": 6,
        "materia_nombre": "Biología y Anatomía",
        "tema_nombre": "Fisiología renal",
        "enunciado": (
            "¿Cuál es la unidad funcional del riñón donde se filtra la sangre?"
        ),
        "alternativas": {
            "A": "Uréter",
            "B": "Nefrona",
            "C": "Pelvis renal",
            "D": "Cápsula de Gerota",
            "E": "Uretra",
        },
        "alternativa_correcta": "B",
        "justificacion": (
            "La nefrona (glomérulo + túbulos) es la unidad funcional renal "
            "responsable de filtración, reabsorción y secreción."
        ),
        "factor_ponderacion": FACTOR_BIOLOGIA_ANATOMIA,
    },
    {
        "id_pregunta": 7,
        "materia_nombre": "Biología y Anatomía",
        "tema_nombre": "Sistema endocrino",
        "enunciado": (
            "¿Qué glándula libera insulina para regular la glucemia?"
        ),
        "alternativas": {
            "A": "Tiroides",
            "B": "Hipófisis anterior",
            "C": "Suprarrenal",
            "D": "Páncreas (islotes de Langerhans)",
            "E": "Paratiroides",
        },
        "alternativa_correcta": "D",
        "justificacion": (
            "Las células β de los islotes pancreáticos secretan insulina, "
            "que favorece la captación de glucosa por los tejidos."
        ),
        "factor_ponderacion": FACTOR_BIOLOGIA_ANATOMIA,
    },
    {
        "id_pregunta": 8,
        "materia_nombre": "Biología y Anatomía",
        "tema_nombre": "Citología",
        "enunciado": (
            "¿Cuál organelo es la principal sede de la respiración celular "
            "y producción de ATP?"
        ),
        "alternativas": {
            "A": "Ribosoma",
            "B": "Aparato de Golgi",
            "C": "Mitocondria",
            "D": "Retículo endoplásmico liso",
            "E": "Lisosoma",
        },
        "alternativa_correcta": "C",
        "justificacion": (
            "La mitocondria realiza el ciclo de Krebs y la fosforilación "
            "oxidativa, generando la mayor parte del ATP celular."
        ),
        "factor_ponderacion": FACTOR_BIOLOGIA_ANATOMIA,
    },
]

OPTION_LETTERS = ("A", "B", "C", "D", "E")
