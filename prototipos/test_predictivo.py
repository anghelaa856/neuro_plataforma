import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

print("Iniciando la fase de entrenamiento del Motor Predictivo...\n")

# 1. SIMULACIÓN DEL ARCHIVO CSV (Memoria Activa)
# Formato de cada fila: [Racha, Factor_Facilidad, Intervalo_Dias]

# Casos donde el estudiante SÍ RECUERDA la tarjeta (Etiqueta = 1)
# (Suelen tener rachas decentes o intervalos proporcionales)
datos_recuerda = [
    [3, 2.5, 2],
    [5, 2.6, 5],
    [10, 2.8, 15],
    [2, 2.4, 1],
    [7, 2.7, 8],
    [12, 2.9, 20],
    [4, 2.5, 3]
]
etiquetas_recuerda = [1] * len(datos_recuerda)

# Casos donde el estudiante OLVIDA la tarjeta (Etiqueta = 0)
# (Intervalos de espera muy largos para rachas muy bajas)
datos_olvida = [
    [0, 1.3, 10], 
    [1, 1.5, 7],
    [2, 2.0, 30], # 30 días es mucho para una racha de solo 2
    [0, 1.8, 5],
    [1, 1.4, 14],
    [3, 2.1, 40]  # 40 días para racha 3 garantiza el olvido
]
etiquetas_olvida = [0] * len(datos_olvida)

# Unimos los datos para crear nuestro "dataset" completo
X = np.array(datos_recuerda + datos_olvida)
y = np.array(etiquetas_recuerda + etiquetas_olvida)

# 2. DIVISIÓN 80/20 (Entrenamiento y Validación)
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.20, random_state=42)

# 3. ENTRENAMIENTO (Gradient Boosting)
# El modelo creará múltiples árboles de decisión corrigiendo sus propios errores
modelo_gb = GradientBoostingClassifier(n_estimators=50, random_state=42)
modelo_gb.fit(X_train, y_train)

# 4. PRUEBA DE VALIDACIÓN (El 20% que el modelo no ha visto)
predicciones = modelo_gb.predict(X_test)
precision = accuracy_score(y_test, predicciones)

print("--- RESULTADOS DE VALIDACIÓN ---")
print(f"Precisión del modelo en datos nuevos: {precision * 100:.1f}%\n")

# 5. PREDICCIÓN EN VIVO (Simulando el uso real)
# Supongamos que el sistema está a punto de mostrarte una tarjeta con:
# Racha = 1 | Factor de Facilidad = 1.5 | Han pasado 21 días (Intervalo)
tarjeta_dificil = np.array([[1, 1.5, 21]])

# Le pedimos al modelo que no solo nos dé el resultado, sino los porcentajes
probabilidades = modelo_gb.predict_proba(tarjeta_dificil)[0]

print("--- EVALUANDO PRÓXIMA TARJETA EN TIEMPO REAL ---")
print("Características: Racha=1, Factor=1.5, Días transcurridos=21")
print(f"⚠️ Probabilidad de que la OLVIDES:   {probabilidades[0] * 100:.1f}%")
print(f"🧠 Probabilidad de que la RECUERDES: {probabilidades[1] * 100:.1f}%")
