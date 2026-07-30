-- =============================================================================
-- Neuro Plataforma — DDL Simulador de Admisión UNA (Área Biomédicas)
-- Dialecto: PostgreSQL 15+ (compatible Neon.tech)
--
-- Principios arquitectónicos:
--   1. Catálogo inmutable  → taxonomía oficial (prospecto), no etiquetas LLM.
--   2. Temario dinámico    → puente academia/libro → materia oficial.
--   3. Banco append-only   → ítems versionables; dificultad NO se persiste fija.
--   4. Ledger (INSERT-only)→ series temporales; nunca UPDATE de intentos.
--   5. Sesión de simulacro → contexto de examen (60 Q / 120 min / máx 3000).
--
-- Convención: snake_case español; PK id_<entidad>; FK <entidad>_id.
-- Reutiliza usuarios.id_usuario del esquema vigente.
-- =============================================================================

BEGIN;

-- Extensiones útiles para UUID de sesión y búsqueda textual futura
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -----------------------------------------------------------------------------
-- 1. catalogo_materias  (Catálogo inmutable = Prospecto oficial Área Biomédicas)
-- -----------------------------------------------------------------------------
-- Decisión: una fila por asignatura oficial. cantidad_preguntas + factor_ponderacion
-- vienen de la Tabla 4 del Reglamento General de Admisión UNA-Puno 2026.
-- Puntaje ponderado por ítem correcto = 10 * factor_ponderacion.
-- Σ (10 × cantidad_preguntas × factor) ≈ 3000 (máximo oficial).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS catalogo_materias (
    id_materia          SMALLSERIAL PRIMARY KEY,
    codigo              SMALLINT        NOT NULL,          -- COD. oficial 1..18
    nombre              VARCHAR(80)     NOT NULL,
    cantidad_preguntas  SMALLINT        NOT NULL,
    -- Puntos base por ítem según reglamento (siempre 10 si correcta)
    puntos_correcta     SMALLINT        NOT NULL DEFAULT 10,
    puntos_en_blanco    SMALLINT        NOT NULL DEFAULT 2,
    puntos_incorrecta   SMALLINT        NOT NULL DEFAULT 0,
    -- Coeficiente [3] de la Tabla 4 Biomédicas
    factor_ponderacion  NUMERIC(6, 3)   NOT NULL,
    -- Subtotal teórico si todas correctas: 10 * cantidad * factor ≈ contribución a 3000
    area_examen         VARCHAR(30)     NOT NULL DEFAULT 'BIOMEDICAS',
    vigente_desde       DATE            NOT NULL DEFAULT DATE '2026-01-01',
    activo              BOOLEAN         NOT NULL DEFAULT TRUE,
    creado_en           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_catalogo_materias_codigo_area
        UNIQUE (area_examen, codigo),
    CONSTRAINT uq_catalogo_materias_nombre_area
        UNIQUE (area_examen, nombre),
    CONSTRAINT ck_catalogo_cantidad_preguntas
        CHECK (cantidad_preguntas BETWEEN 1 AND 10),
    CONSTRAINT ck_catalogo_puntos_regla_una
        CHECK (
            puntos_correcta = 10
            AND puntos_en_blanco = 2
            AND puntos_incorrecta = 0
        ),
    CONSTRAINT ck_catalogo_factor_positivo
        CHECK (factor_ponderacion > 0),
    CONSTRAINT ck_catalogo_area
        CHECK (area_examen IN ('BIOMEDICAS', 'SOCIALES', 'INGENIERIAS'))
);

COMMENT ON TABLE catalogo_materias IS
    'Prospecto oficial UNA: 18 asignaturas inmutables del Área Biomédicas. '
    'Fuente de verdad para cruzar rendimiento vs syllabus (anti-syllabus-fantasma).';
COMMENT ON COLUMN catalogo_materias.factor_ponderacion IS
    'Coeficiente [3] Tabla 4 Biomédicas. Puntaje ponderado = puntos_base * factor.';

-- Seed oficial Biomédicas (Tabla 4). Idempotente por UNIQUE (area, codigo).
INSERT INTO catalogo_materias
    (codigo, nombre, cantidad_preguntas, factor_ponderacion, area_examen)
VALUES
    (1,  'Aritmética',               3, 3.331, 'BIOMEDICAS'),
    (2,  'Álgebra',                  3, 3.202, 'BIOMEDICAS'),
    (3,  'Geometría',                3, 3.301, 'BIOMEDICAS'),
    (4,  'Trigonometría',            3, 3.404, 'BIOMEDICAS'),
    (5,  'Física',                   3, 5.505, 'BIOMEDICAS'),
    (6,  'Química',                  5, 6.623, 'BIOMEDICAS'),
    (7,  'Biología y Anatomía',      6, 7.816, 'BIOMEDICAS'),
    (8,  'Psicología y Filosofía',   4, 4.006, 'BIOMEDICAS'),
    (9,  'Geografía',                2, 2.800, 'BIOMEDICAS'),
    (10, 'Historia',                 2, 3.302, 'BIOMEDICAS'),
    (11, 'Educación Cívica',         2, 3.571, 'BIOMEDICAS'),
    (12, 'Economía',                 2, 3.406, 'BIOMEDICAS'),
    (13, 'Comunicación',             4, 3.302, 'BIOMEDICAS'),
    (14, 'Literatura',               2, 2.805, 'BIOMEDICAS'),
    (15, 'Razonamiento Matemático',  6, 7.201, 'BIOMEDICAS'),
    (16, 'Razonamiento Verbal',      6, 7.201, 'BIOMEDICAS'),
    (17, 'Inglés',                   2, 4.087, 'BIOMEDICAS'),
    (18, 'Quechua y aimara',         2, 4.087, 'BIOMEDICAS')
ON CONFLICT (area_examen, codigo) DO NOTHING;

-- Inmutabilidad operativa: bloquear UPDATE/DELETE del catálogo sembrado
CREATE OR REPLACE FUNCTION fn_bloquear_mutacion_catalogo()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'catalogo_materias es inmutable (prospecto oficial). Use un nuevo ciclo vigente_desde.';
END;
$$;

DROP TRIGGER IF EXISTS trg_catalogo_materias_inmutable ON catalogo_materias;
CREATE TRIGGER trg_catalogo_materias_inmutable
    BEFORE UPDATE OR DELETE ON catalogo_materias
    FOR EACH ROW
    EXECUTE FUNCTION fn_bloquear_mutacion_catalogo();

-- -----------------------------------------------------------------------------
-- 2. temas_estudio  (Temario dinámico = puente academias/libros → catálogo)
-- -----------------------------------------------------------------------------
-- Decisión: el LLM etiquetaba texto libre; ahora toda carpeta/tema DEBE mapear
-- a una materia oficial vía FK NOT NULL. Así el analytics Pandas puede agrupar
-- por catalogo_materias sin joins frágiles por string.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS temas_estudio (
    id_tema             BIGSERIAL PRIMARY KEY,
    materia_id          SMALLINT        NOT NULL
                            REFERENCES catalogo_materias (id_materia)
                            ON UPDATE RESTRICT
                            ON DELETE RESTRICT,
    nombre              VARCHAR(200)    NOT NULL,
    descripcion         TEXT,
    origen_contenido    VARCHAR(40),                 -- pdf | academia | apuntes | editorial
    slug                VARCHAR(220)    NOT NULL,
    activo              BOOLEAN         NOT NULL DEFAULT TRUE,
    creado_en           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    actualizado_en      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_temas_estudio_materia_slug
        UNIQUE (materia_id, slug),
    CONSTRAINT ck_temas_nombre_no_vacio
        CHECK (length(btrim(nombre)) >= 2)
);

COMMENT ON TABLE temas_estudio IS
    'Carpetas/temas de estudio (libros, academias). FK obligatoria al prospecto: '
    'elimina etiquetas libres del LLM como eje de analítica.';

CREATE INDEX IF NOT EXISTS idx_temas_estudio_materia_id
    ON temas_estudio (materia_id)
    WHERE activo;

CREATE INDEX IF NOT EXISTS idx_temas_estudio_nombre_trgm_ready
    ON temas_estudio (lower(nombre));

-- -----------------------------------------------------------------------------
-- 3. banco_preguntas  (Bóveda inmutable de ítems)
-- -----------------------------------------------------------------------------
-- Decisión: el contenido del ítem no se sobrescribe. Si hay error editorial,
-- se desactiva (activa=FALSE) y se inserta una versión nueva. La dificultad
-- NO se almacena como verdad fija: se calcula luego como tasa_error histórica
-- desde historial_intentos (vista materializada / job Pandas).
-- Alternativas: JSONB ordenado A..E (formato ficha óptica UNA: 5 opciones).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS banco_preguntas (
    id_pregunta             BIGSERIAL PRIMARY KEY,
    tema_id                 BIGINT          NOT NULL
                                REFERENCES temas_estudio (id_tema)
                                ON UPDATE RESTRICT
                                ON DELETE RESTRICT,
    enunciado               TEXT            NOT NULL,
    -- {"A":"...","B":"...","C":"...","D":"...","E":"..."}
    alternativas            JSONB           NOT NULL,
    alternativa_correcta    CHAR(1)         NOT NULL,
    justificacion           TEXT            NOT NULL,
    -- Metadatos de curaduría (no de dificultad dinámica)
    fuente                  VARCHAR(120),
    nombre_archivo_fuente   VARCHAR(255),            -- nombre del PDF/upload de origen
    -- NULL = banco oficial (admin); ID = pregunta privada del alumno (Fase 7)
    propietario_usuario_id  BIGINT,
    anio_referencia         SMALLINT,
    hash_contenido          CHAR(64)        NOT NULL,  -- SHA-256 del enunciado+alts (dedup)
    activa                  BOOLEAN         NOT NULL DEFAULT TRUE,
    creado_en               TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT ck_banco_alternativa_correcta
        CHECK (alternativa_correcta IN ('A', 'B', 'C', 'D', 'E')),
    CONSTRAINT ck_banco_alternativas_cinco_claves
        CHECK (
            alternativas ? 'A'
            AND alternativas ? 'B'
            AND alternativas ? 'C'
            AND alternativas ? 'D'
            AND alternativas ? 'E'
        ),
    CONSTRAINT ck_banco_enunciado_no_vacio
        CHECK (length(btrim(enunciado)) >= 10),
    CONSTRAINT ck_banco_justificacion_no_vacia
        CHECK (length(btrim(justificacion)) >= 5)
);

-- Compat DBs pre-Fase 7/8: CREATE TABLE IF NOT EXISTS no añade columnas a tablas viejas.
-- Estos ALTER DEBEN ir antes de COMMENT/INDEX que referencien las columnas.
ALTER TABLE banco_preguntas
    ADD COLUMN IF NOT EXISTS nombre_archivo_fuente VARCHAR(255);
ALTER TABLE banco_preguntas
    ADD COLUMN IF NOT EXISTS propietario_usuario_id BIGINT;

COMMENT ON TABLE banco_preguntas IS
    'Bóveda de ítems append-only. Sin columna de dificultad fija: se deriva '
    'de historial_intentos (tasa de error). Soft-delete vía activa=FALSE.';
COMMENT ON COLUMN banco_preguntas.alternativas IS
    'Mapa JSONB A..E alineado a ficha óptica UNA (5 alternativas).';
COMMENT ON COLUMN banco_preguntas.nombre_archivo_fuente IS
    'Nombre del archivo PDF/upload del que se extrajo el ítem (trazabilidad de ingesta).';
COMMENT ON COLUMN banco_preguntas.propietario_usuario_id IS
    'NULL = ítem del banco oficial (admin). Valor = pregunta privada del alumno (aislamiento).';

-- Dedup por dueño (0 = oficial). Evita que la guía de un alumno contamine a otro.
CREATE UNIQUE INDEX IF NOT EXISTS uq_banco_hash_propietario
    ON banco_preguntas (hash_contenido, (COALESCE(propietario_usuario_id, 0)));

CREATE INDEX IF NOT EXISTS idx_banco_preguntas_tema_id
    ON banco_preguntas (tema_id)
    WHERE activa;

CREATE INDEX IF NOT EXISTS idx_banco_preguntas_activa
    ON banco_preguntas (activa);

CREATE INDEX IF NOT EXISTS idx_banco_preguntas_propietario
    ON banco_preguntas (propietario_usuario_id)
    WHERE activa;

-- Impide mutar enunciado/alternativas/clave (inmutabilidad de contenido)
CREATE OR REPLACE FUNCTION fn_bloquear_mutacion_banco_contenido()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'banco_preguntas no admite DELETE. Desactive con activa=FALSE.';
    END IF;

    IF NEW.enunciado IS DISTINCT FROM OLD.enunciado
       OR NEW.alternativas IS DISTINCT FROM OLD.alternativas
       OR NEW.alternativa_correcta IS DISTINCT FROM OLD.alternativa_correcta
       OR NEW.justificacion IS DISTINCT FROM OLD.justificacion
       OR NEW.hash_contenido IS DISTINCT FROM OLD.hash_contenido
       OR NEW.tema_id IS DISTINCT FROM OLD.tema_id
    THEN
        RAISE EXCEPTION
            'Contenido de banco_preguntas es inmutable. Inserte una nueva versión.';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_banco_preguntas_inmutable ON banco_preguntas;
CREATE TRIGGER trg_banco_preguntas_inmutable
    BEFORE UPDATE OR DELETE ON banco_preguntas
    FOR EACH ROW
    EXECUTE FUNCTION fn_bloquear_mutacion_banco_contenido();

-- -----------------------------------------------------------------------------
-- 5. sesiones_simulacro  (Entorno de examen — se crea ANTES del ledger)
-- -----------------------------------------------------------------------------
-- Decisión: agrupa intentos bajo reglas estrictas UNA. Los parámetros de
-- examen se congelan en la fila (snapshot) para que un cambio futuro del
-- reglamento no reescrica simulacros históricos.
-- Puntaje máximo oficial ponderado = 3000.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sesiones_simulacro (
    id_sesion               BIGSERIAL PRIMARY KEY,
    usuario_id              BIGINT          NOT NULL
                                REFERENCES usuarios (id_usuario)
                                ON UPDATE RESTRICT
                                ON DELETE CASCADE,
    area_examen             VARCHAR(30)     NOT NULL DEFAULT 'BIOMEDICAS',
    -- Snapshot de reglas (no leer defaults de app en runtime histórico)
    total_preguntas         SMALLINT        NOT NULL DEFAULT 60,
    tiempo_maximo_minutos   SMALLINT        NOT NULL DEFAULT 120,
    puntos_correcta         SMALLINT        NOT NULL DEFAULT 10,
    puntos_en_blanco        SMALLINT        NOT NULL DEFAULT 2,
    puntos_incorrecta       SMALLINT        NOT NULL DEFAULT 0,
    puntaje_maximo_ponderado NUMERIC(8, 2)  NOT NULL DEFAULT 3000,
    -- Ciclo de vida
    estado                  VARCHAR(20)     NOT NULL DEFAULT 'en_curso',
    iniciada_en             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    finalizada_en           TIMESTAMPTZ,
    -- Agregados (materializados al cerrar; el ledger sigue siendo la verdad)
    respuestas_correctas    SMALLINT,
    respuestas_incorrectas  SMALLINT,
    respuestas_en_blanco    SMALLINT,
    puntaje_bruto           NUMERIC(8, 2),      -- suma de 0/2/10 sin ponderar
    puntaje_ponderado       NUMERIC(10, 3),     -- Σ (puntos_base × factor_materia)
    tiempo_total_ms         BIGINT,
    -- Semilla para reproducibilidad del muestreo estratificado por materia
    seed_muestreo           BIGINT,
    notas                   TEXT,

    CONSTRAINT ck_sesion_area
        CHECK (area_examen IN ('BIOMEDICAS', 'SOCIALES', 'INGENIERIAS')),
    CONSTRAINT ck_sesion_total_60
        CHECK (total_preguntas = 60),
    CONSTRAINT ck_sesion_tiempo_120
        CHECK (tiempo_maximo_minutos = 120),
    CONSTRAINT ck_sesion_regla_puntaje
        CHECK (
            puntos_correcta = 10
            AND puntos_en_blanco = 2
            AND puntos_incorrecta = 0
        ),
    CONSTRAINT ck_sesion_max_3000
        CHECK (puntaje_maximo_ponderado = 3000),
    CONSTRAINT ck_sesion_estado
        CHECK (estado IN ('en_curso', 'finalizada', 'abandonada', 'anulada')),
    CONSTRAINT ck_sesion_cierre
        CHECK (
            (estado = 'en_curso' AND finalizada_en IS NULL)
            OR (estado <> 'en_curso' AND finalizada_en IS NOT NULL)
        )
);

COMMENT ON TABLE sesiones_simulacro IS
    'Contenedor de simulacro UNA: 60 preguntas, 120 min, scoring +10/+2/0, '
    'máximo ponderado 3000. Snapshot de reglas para auditoría histórica.';

CREATE INDEX IF NOT EXISTS idx_sesiones_simulacro_usuario_iniciada
    ON sesiones_simulacro (usuario_id, iniciada_en DESC);

CREATE INDEX IF NOT EXISTS idx_sesiones_simulacro_estado
    ON sesiones_simulacro (estado)
    WHERE estado = 'en_curso';

-- -----------------------------------------------------------------------------
-- 4. historial_intentos  (Ledger append-only — cura la amnesia de datos)
-- -----------------------------------------------------------------------------
-- Decisión: SOLO INSERT. Cada intento es un evento inmutable en el tiempo.
-- Es la fuente de verdad para series temporales con Pandas (accuracy, IRT,
-- curva de olvido, dificultad empírica por pregunta_id).
-- sesion_id NULL = práctica libre / SRS; NOT NULL = dentro de un simulacro.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS historial_intentos (
    id_intento              BIGSERIAL PRIMARY KEY,
    usuario_id              BIGINT          NOT NULL
                                REFERENCES usuarios (id_usuario)
                                ON UPDATE RESTRICT
                                ON DELETE CASCADE,
    pregunta_id             BIGINT          NOT NULL
                                REFERENCES banco_preguntas (id_pregunta)
                                ON UPDATE RESTRICT
                                ON DELETE RESTRICT,
    sesion_id               BIGINT
                                REFERENCES sesiones_simulacro (id_sesion)
                                ON UPDATE RESTRICT
                                ON DELETE SET NULL,
    -- Posición 1..60 dentro del simulacro (NULL en práctica libre)
    orden_en_sesion         SMALLINT,
    fecha_hora              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    tiempo_respuesta_ms     INTEGER         NOT NULL,
    -- NULL = en blanco (no marcó); A..E = marcada
    alternativa_marcada     CHAR(1),
    es_correcta             BOOLEAN,                -- NULL si en blanco
    -- Puntos base UNA antes de ponderar: 10 | 2 | 0
    puntaje_obtenido        SMALLINT        NOT NULL,
    -- Factor de la materia al momento del intento (desnormalizado para analytics)
    factor_ponderacion_aplicado NUMERIC(6, 3),
    puntaje_ponderado       NUMERIC(10, 3),

    CONSTRAINT ck_historial_tiempo_no_negativo
        CHECK (tiempo_respuesta_ms >= 0),
    CONSTRAINT ck_historial_alternativa
        CHECK (
            alternativa_marcada IS NULL
            OR alternativa_marcada IN ('A', 'B', 'C', 'D', 'E')
        ),
    CONSTRAINT ck_historial_consistencia_respuesta
        CHECK (
            -- En blanco
            (alternativa_marcada IS NULL
                AND es_correcta IS NULL
                AND puntaje_obtenido = 2)
            -- Marcada correcta
            OR (alternativa_marcada IS NOT NULL
                AND es_correcta IS TRUE
                AND puntaje_obtenido = 10)
            -- Marcada incorrecta
            OR (alternativa_marcada IS NOT NULL
                AND es_correcta IS FALSE
                AND puntaje_obtenido = 0)
        ),
    CONSTRAINT ck_historial_orden_sesion
        CHECK (
            orden_en_sesion IS NULL
            OR orden_en_sesion BETWEEN 1 AND 60
        ),
    -- Un intento por pregunta dentro de la misma sesión de simulacro
    CONSTRAINT uq_historial_sesion_pregunta
        UNIQUE (sesion_id, pregunta_id),
    CONSTRAINT uq_historial_sesion_orden
        UNIQUE (sesion_id, orden_en_sesion)
);

COMMENT ON TABLE historial_intentos IS
    'Ledger transaccional INSERT-only. Reemplaza el UPDATE sobre tarjetas. '
    'Fuente de verdad para series temporales (Pandas / IRT / dificultad empírica).';
COMMENT ON COLUMN historial_intentos.puntaje_obtenido IS
    'Puntos base reglamento: 10 correcta, 2 en blanco, 0 incorrecta.';
COMMENT ON COLUMN historial_intentos.puntaje_ponderado IS
    'puntaje_obtenido * factor_ponderacion_aplicado (contribución al techo 3000).';

-- Índices orientados a analítica
CREATE INDEX IF NOT EXISTS idx_historial_usuario_fecha
    ON historial_intentos (usuario_id, fecha_hora DESC);

CREATE INDEX IF NOT EXISTS idx_historial_pregunta_resultado
    ON historial_intentos (pregunta_id, es_correcta, fecha_hora DESC);

CREATE INDEX IF NOT EXISTS idx_historial_sesion_id
    ON historial_intentos (sesion_id)
    WHERE sesion_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_historial_usuario_pregunta
    ON historial_intentos (usuario_id, pregunta_id, fecha_hora DESC);

-- Ledger: prohibir UPDATE y DELETE
CREATE OR REPLACE FUNCTION fn_bloquear_mutacion_historial()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'historial_intentos es append-only (ledger). Solo se permiten INSERT.';
END;
$$;

DROP TRIGGER IF EXISTS trg_historial_intentos_append_only ON historial_intentos;
CREATE TRIGGER trg_historial_intentos_append_only
    BEFORE UPDATE OR DELETE ON historial_intentos
    FOR EACH ROW
    EXECUTE FUNCTION fn_bloquear_mutacion_historial();

-- -----------------------------------------------------------------------------
-- Vista analítica: dificultad empírica (no persistida en el banco)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_dificultad_empirica_preguntas AS
SELECT
    p.id_pregunta,
    p.tema_id,
    count(h.id_intento)                                         AS n_intentos,
    count(h.id_intento) FILTER (WHERE h.es_correcta IS FALSE)   AS n_incorrectas,
    count(h.id_intento) FILTER (WHERE h.es_correcta IS TRUE)    AS n_correctas,
    count(h.id_intento) FILTER (WHERE h.alternativa_marcada IS NULL) AS n_en_blanco,
    CASE
        WHEN count(h.id_intento) FILTER (WHERE h.alternativa_marcada IS NOT NULL) = 0
            THEN NULL
        ELSE round(
            count(h.id_intento) FILTER (WHERE h.es_correcta IS FALSE)::NUMERIC
            / nullif(
                count(h.id_intento) FILTER (WHERE h.alternativa_marcada IS NOT NULL),
                0
              ),
            4
        )
    END AS tasa_error,
    round(avg(h.tiempo_respuesta_ms)::NUMERIC, 0) AS tiempo_ms_promedio
FROM banco_preguntas p
LEFT JOIN historial_intentos h ON h.pregunta_id = p.id_pregunta
GROUP BY p.id_pregunta, p.tema_id;

COMMENT ON VIEW vw_dificultad_empirica_preguntas IS
    'Dificultad dinámica derivada del ledger. Sustituye nivel_dificultad estático.';

-- -----------------------------------------------------------------------------
-- Vista: cobertura de prospecto por estudiante (anti-syllabus-fantasma)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW vw_rendimiento_por_materia_oficial AS
SELECT
    h.usuario_id,
    m.id_materia,
    m.codigo,
    m.nombre AS materia,
    m.cantidad_preguntas AS cupo_oficial_examen,
    m.factor_ponderacion,
    count(h.id_intento) AS n_intentos,
    round(
        avg(CASE WHEN h.es_correcta THEN 1.0 ELSE 0.0 END)::NUMERIC,
        4
    ) AS accuracy,
    round(sum(h.puntaje_ponderado)::NUMERIC, 3) AS suma_puntaje_ponderado
FROM historial_intentos h
JOIN banco_preguntas p   ON p.id_pregunta = h.pregunta_id
JOIN temas_estudio t     ON t.id_tema = p.tema_id
JOIN catalogo_materias m ON m.id_materia = t.materia_id
GROUP BY h.usuario_id, m.id_materia, m.codigo, m.nombre,
         m.cantidad_preguntas, m.factor_ponderacion;

COMMENT ON VIEW vw_rendimiento_por_materia_oficial IS
    'Cruza intentos del estudiante con el prospecto oficial (18 materias Biomédicas).';

COMMIT;

-- =============================================================================
-- Diagrama lógico (referencia)
--
--  usuarios 1──* sesiones_simulacro 1──* historial_intentos *──1 banco_preguntas
--      │                                      │
--      └──────────* historial_intentos (práctica libre, sesion_id NULL)
--
--  catalogo_materias 1──* temas_estudio 1──* banco_preguntas
--
-- Muestreo de simulacro: estratificar por catalogo_materias.cantidad_preguntas
-- (ej. 6 de Biología, 5 de Química, …) hasta sumar 60.
-- =============================================================================
