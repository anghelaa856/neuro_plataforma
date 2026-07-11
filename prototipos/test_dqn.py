import torch
import torch.nn as nn

print("Construyendo la Red Neuronal (Deep Q-Network)...\n")

# 1. DEFINIMOS LA ARQUITECTURA DEL CEREBRO
class RedNeuronalEstudio(nn.Module):
    def __init__(self):
        super(RedNeuronalEstudio, self).__init__()
        
        # Capa de Entrada: Recibe 3 datos (Racha, Factor_Facilidad, Intervalo_Dias)
        self.capa_entrada = nn.Linear(3, 16)
        
        # Capa Oculta: 16 neuronas que procesan la lógica usando la función ReLU
        self.capa_oculta = nn.ReLU()
        
        # Capa de Salida: Devuelve 4 valores (las 4 posibles acciones/intervalos a elegir)
        # Acción 0: Revisar mañana (1 día)
        # Acción 1: Revisar en 3 días
        # Acción 2: Revisar en 7 días
        # Acción 3: Revisar en 15 días
        self.capa_salida = nn.Linear(16, 4)

    def forward(self, x):
        """Así es como viaja la información dentro del cerebro"""
        x = self.capa_entrada(x)
        x = self.capa_oculta(x)
        x = self.capa_salida(x)
        return x

# 2. INSTANCIAMOS EL MODELO
modelo_dqn = RedNeuronalEstudio()

# 3. SIMULAMOS UN ESTADO DEL ENTORNO (El alumno frente a la tarjeta)
# Estado: Racha = 4, Factor_Facilidad = 2.5, Han pasado = 5 días
estado_actual = torch.tensor([4.0, 2.5, 5.0], dtype=torch.float32)

# 4. LE PEDIMOS AL CEREBRO QUE TOME UNA DECISIÓN
# Le pasamos el estado por la red neuronal
valores_q = modelo_dqn(estado_actual)

# La IA elige la acción que tenga el valor más alto (argmax)
accion_elegida = torch.argmax(valores_q).item()

# Diccionario para traducir la acción de la IA a días reales
diccionario_acciones = {
    0: "+1 día (Mañana)",
    1: "+3 días",
    2: "+7 días",
    3: "+15 días"
}

# 5. RESULTADOS DE LA AUDITORÍA
print("--- ANÁLISIS DE LA RED NEURONAL ---")
print(f"Estado del Estudiante: Racha=4 | Factor=2.5 | Días=5")
print(f"Valores 'Q' crudos (Puntaje por acción): {valores_q.detach().numpy()}")
print(f"\n🧠 DECISIÓN DE LA IA:")
print(f"El modelo eligió la Acción {accion_elegida}: Asignar un intervalo de {diccionario_acciones[accion_elegida]}")
