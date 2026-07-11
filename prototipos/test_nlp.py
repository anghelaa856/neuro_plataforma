from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity

print("Cargando el modelo de IA... (Esto puede tomar unos segundos la primera vez)")
# Descarga y carga un modelo ultra-rápido que entiende el significado del texto
modelo = SentenceTransformer('all-MiniLM-L6-v2') 

def calificar_respuesta(referencia, respuesta_estudiante):
    """Convierte los textos a vectores y calcula qué tan similares son."""
    
    # 1. Transformar texto en números (Embeddings de 384 dimensiones)
    vector_ref = modelo.encode([referencia])
    vector_est = modelo.encode([respuesta_estudiante])
    
    # 2. Calcular la Distancia Coseno (devuelve un valor entre 0.0 y 1.0)
    similitud = cosine_similarity(vector_ref, vector_est)[0][0]
    
    # 3. Convertir la similitud a tu escala de 0 a 5
    # (Ajustamos un poco la fórmula porque la similitud pura rara vez es 1.0 perfecta)
    calidad_ia = similitud * 5
    
    # Redondeamos a un decimal para que sea más legible
    return round(calidad_ia, 1)

# ==========================================
# ZONA DE PRUEBAS (Happy Path & Sad Path)
# ==========================================

# El conocimiento puro que está en tu Catálogo Maestro
texto_referencia = "La fotosíntesis es el proceso bioquímico mediante el cual las plantas convierten la luz solar, el agua y el dióxido de carbono en oxígeno y energía en forma de azúcar."

# PRUEBA 1: Ruta Feliz (Happy Path)
# El estudiante entiende el concepto pero usa sinónimos y otra estructura.
respuesta_feliz = "Es cuando las plantas usan el sol y el agua para crear su propio alimento y soltar oxígeno."
nota_feliz = calificar_respuesta(texto_referencia, respuesta_feliz)

# PRUEBA 2: Ruta Triste (Sad Path)
# El estudiante habla de algo nada que ver (o intenta engañar al sistema).
respuesta_triste = "El sol es una estrella muy grande que da calor a la tierra por las mañanas."
nota_triste = calificar_respuesta(texto_referencia, respuesta_triste)

print("\n--- RESULTADOS DE LA AUDITORÍA DE IA ---")
print(f"Texto de Referencia: '{texto_referencia}'\n")

print(f"✅ RUTA FELIZ: '{respuesta_feliz}'")
print(f"👉 Calificación de la IA: {nota_feliz} / 5.0\n")

print(f"❌ RUTA TRISTE: '{respuesta_triste}'")
print(f"👉 Calificación de la IA: {nota_triste} / 5.0")
