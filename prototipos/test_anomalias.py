import numpy as np
from sklearn.ensemble import IsolationForest

print("Entrenando al Detector de Anomalías...")

# 1. Simulamos el historial del estudiante (Tiempo en milisegundos)
# Imagina que normalmente tardas entre 7,000 y 9,000 ms en responder.
tiempos_normales = [8100, 7500, 8900, 7800, 8200, 7900, 8500, 7700, 8300, 8000]

# Ahora inyectamos las anomalías (trampa/fatiga)
tiempos_sospechosos = [
    400,    # ¡Demasiado rápido! (Hizo clic sin leer)
    45000,  # ¡Demasiado lento! (Se fue a tomar un café o se distrajo)
    8150    # Un tiempo normal para comprobar que no marque todo como error
]

# Juntamos todos los datos y los preparamos para Scikit-Learn
todos_los_tiempos = tiempos_normales + tiempos_sospechosos
# Scikit-Learn requiere que los datos estén en una matriz 2D (columna)
X = np.array(todos_los_tiempos).reshape(-1, 1)

# 2. Configuramos el Isolation Forest
# contamination=0.15 significa que estimamos que el 15% de los datos podrían ser anomalías
modelo_anomalias = IsolationForest(contamination=0.15, random_state=42)

# 3. Entrenamos el modelo y le pedimos que haga predicciones al mismo tiempo
predicciones = modelo_anomalias.fit_predict(X)

# 4. Resultados de la Auditoría
print("\n--- RESULTADOS DEL DETECTOR DE ANOMALÍAS ---")
print("Leyenda: [ 1 = Normal | -1 = Anomalía Detectada ]\n")

for tiempo, prediccion in zip(todos_los_tiempos, predicciones):
    estado = "✅ NORMAL" if prediccion == 1 else "🚨 ANOMALÍA"
    print(f"Tiempo: {tiempo:5d} ms --> {estado}")
