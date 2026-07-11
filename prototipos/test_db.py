import unittest
import psycopg2

# Configuración de tu base de datos local
DB_CONFIG = {
    "dbname": "sistema_estudio",
    "user": "postgres", 
    "password": "Conexion@9", # ¡Cámbialo por tu contraseña!
    "host": "localhost",
    "port": "5432"
}

class TestBaseDeDatos(unittest.TestCase):
    
    def setUp(self):
        """Se ejecuta ANTES de cada prueba. Prepara la conexión."""
        self.conn = psycopg2.connect(**DB_CONFIG)
        self.cur = self.conn.cursor()

    def tearDown(self):
        """Se ejecuta DESPUÉS de cada prueba. Limpia todo."""
        # Hacemos rollback para que los datos de prueba NO se guarden permanentemente
        # y nuestra base de datos se mantenga limpia.
        self.conn.rollback() 
        self.cur.close()
        self.conn.close()

    def test_insercion_y_recuperacion(self):
        """Prueba central: Inserta un dato en el Catálogo Maestro y lo lee para verificar."""
        
        # 1. Insertar un dato de prueba
        self.cur.execute("""
            INSERT INTO catalogo_maestro (area, tema_especifico, tipo_conocimiento, contenido_referencia)
            VALUES ('Tecnología', 'Conexión Python-Postgres', 'Concepto', 'Este es un texto de prueba.')
            RETURNING id_tema;
        """)
        
        # Capturamos el ID que PostgreSQL le asignó automáticamente
        id_generado = self.cur.fetchone()[0]
        
        # 2. Recuperar el dato recién insertado
        self.cur.execute("""
            SELECT area, tema_especifico FROM catalogo_maestro WHERE id_tema = %s;
        """, (id_generado,))
        
        registro_recuperado = self.cur.fetchone()
        
        # 3. Aserciones (Afirmaciones de prueba)
        # Comprobamos que lo que enviamos es exactamente lo que recibimos
        self.assertIsNotNone(registro_recuperado, "El registro no se guardó en la base de datos.")
        self.assertEqual(registro_recuperado[0], 'Tecnología', "El área no coincide.")
        self.assertEqual(registro_recuperado[1], 'Conexión Python-Postgres', "El tema no coincide.")
        
        print(f"\n¡Éxito! El registro ID {id_generado} se insertó y recuperó correctamente.")

if __name__ == '__main__':
    unittest.main()
