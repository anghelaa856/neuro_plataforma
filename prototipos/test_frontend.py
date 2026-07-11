import streamlit as st
import time

# Configuración básica de la página
st.set_page_config(page_title="Mi Tutor IA", page_icon="🧠")

st.title("🧠 Sistema de Estudio Inteligente")
st.subheader("Módulo: Contabilidad y Finanzas")

# Simulamos la pantalla donde lees la pregunta
st.info("**Concepto a definir:** ¿Qué es la depreciación de un activo fijo y cómo impacta en los estados financieros?")

# El espacio donde tú escribes la respuesta
respuesta_estudiante = st.text_area("Redacta tu respuesta con tus propias palabras:")

# El botón que desencadenará toda la magia de las Fases 1 a 5
if st.button("Enviar Respuesta"):
    if respuesta_estudiante:
        # Simulamos visualmente el tiempo de procesamiento de los modelos
        with st.spinner("🤖 El Cerebro Semántico está analizando tu respuesta..."):
            time.sleep(1.5) # Pausa simulada
            
        # Simulamos los resultados de tu sistema
        st.success("¡Análisis completado!")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(label="Nota IA (Transformers)", value="4.2 / 5.0")
        with col2:
            st.metric(label="Auditoría (Isolation Forest)", value="Normal", delta="8500 ms", delta_color="off")
        with col3:
            st.metric(label="Próxima Revisión (DQN)", value="+3 Días")
            
        st.write("---")
        st.write("💾 *Los datos han sido guardados en PostgreSQL exitosamente.*")
    else:
        st.warning("⚠️ Por favor, escribe una respuesta antes de enviar.")
