-- Esquema de PostgreSQL del agente académico. La API lo aplica al arrancar; "IF NOT EXISTS"
-- hace que correrlo varias veces no cambie nada.
--
--   conversaciones 1 ── n mensajes 1 ── 0..1 metricas
--                                  1 ── n citas
--                                  1 ── 0..1 auditorias
--
-- Cada pregunta del usuario y cada respuesta del agente es una fila de "mensajes". Las métricas,
-- las citas y las auditorías cuelgan solo de los mensajes del agente.

CREATE TABLE IF NOT EXISTS conversaciones (
    id              UUID PRIMARY KEY,
    creada_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE conversaciones IS 'Una conversación del chat; el navegador guarda su id';

-- Historial por navegador (sin login). Van como ALTER para que una base ya creada también las
-- reciba; en una base nueva se agregan justo después del CREATE
ALTER TABLE conversaciones ADD COLUMN IF NOT EXISTS cliente_id UUID;
ALTER TABLE conversaciones ADD COLUMN IF NOT EXISTS titulo TEXT;
ALTER TABLE conversaciones ADD COLUMN IF NOT EXISTS actualizada_en TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE conversaciones ADD COLUMN IF NOT EXISTS oculta BOOLEAN NOT NULL DEFAULT false;
COMMENT ON COLUMN conversaciones.cliente_id IS 'Navegador dueño de la conversación (id aleatorio en su localStorage)';
COMMENT ON COLUMN conversaciones.titulo IS 'Primera pregunta, recortada: así se ve en el historial';
COMMENT ON COLUMN conversaciones.oculta IS 'Quitada del historial; sus mensajes y métricas se conservan para /admin';
CREATE INDEX IF NOT EXISTS conversaciones_por_cliente
    ON conversaciones (cliente_id, actualizada_en DESC) WHERE NOT oculta;

CREATE TABLE IF NOT EXISTS mensajes (
    id               BIGSERIAL PRIMARY KEY,
    conversacion_id  UUID NOT NULL REFERENCES conversaciones (id) ON DELETE CASCADE,
    rol              TEXT NOT NULL CHECK (rol IN ('usuario', 'agente')),
    contenido        TEXT NOT NULL,
    creado_en        TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE mensajes IS 'Preguntas del usuario y respuestas del agente, en orden';
CREATE INDEX IF NOT EXISTS mensajes_por_conversacion ON mensajes (conversacion_id, id);

CREATE TABLE IF NOT EXISTS metricas (
    mensaje_id          BIGINT PRIMARY KEY REFERENCES mensajes (id) ON DELETE CASCADE,
    modelo              TEXT NOT NULL,          -- LLM que respondió, p. ej. nemotron-3-nano:4b
    tokens_entrada      INTEGER NOT NULL,       -- suma de todas las llamadas al LLM de la pregunta
    tokens_salida       INTEGER NOT NULL,
    llamadas_llm        INTEGER NOT NULL,       -- cada tool agrega una vuelta al LLM
    segundos_total      REAL NOT NULL,          -- de la pregunta a la respuesta
    segundos_busqueda   REAL NOT NULL,          -- de ellos, vectorizar + OpenSearch
    se_puede_responder  BOOLEAN NOT NULL,       -- false = el artículo no cubre la pregunta
    desde_cache         BOOLEAN NOT NULL,       -- true = la respuesta salió de Redis
    consulta_en         TEXT,                   -- la pregunta como la buscó BM25 (en inglés)
    tools               TEXT[] NOT NULL,        -- tools que pidió el LLM, en orden
    error               TEXT                    -- si el modelo no logró una respuesta válida
);
COMMENT ON TABLE metricas IS 'Lo que costó cada respuesta del agente: tokens, tiempos y caché';
ALTER TABLE metricas ADD COLUMN IF NOT EXISTS correcciones INTEGER NOT NULL DEFAULT 0;
COMMENT ON COLUMN metricas.correcciones IS 'Veces que Claude regresó la respuesta para corregirla antes de mostrarla';

CREATE TABLE IF NOT EXISTS citas (
    id           BIGSERIAL PRIMARY KEY,
    mensaje_id   BIGINT NOT NULL REFERENCES mensajes (id) ON DELETE CASCADE,
    fragmento_id TEXT NOT NULL,                 -- id del fragmento en OpenSearch
    seccion      TEXT NOT NULL,
    paginas      INTEGER[] NOT NULL
);
COMMENT ON TABLE citas IS 'Fragmentos del artículo en que se basa cada respuesta';
CREATE INDEX IF NOT EXISTS citas_por_mensaje ON citas (mensaje_id);

CREATE TABLE IF NOT EXISTS auditorias (
    mensaje_id           BIGINT PRIMARY KEY REFERENCES mensajes (id) ON DELETE CASCADE,
    modelo               TEXT NOT NULL,         -- modelo de Claude que auditó
    veredicto            TEXT NOT NULL CHECK (veredicto IN ('aprobada', 'con_observaciones', 'reprobada')),
    fidelidad            TEXT NOT NULL CHECK (fidelidad IN ('fiel', 'parcialmente_fiel', 'no_fiel')),
    relevancia           SMALLINT NOT NULL CHECK (relevancia BETWEEN 1 AND 5),
    citas_correctas      BOOLEAN NOT NULL,      -- los fragmentos citados sostienen la respuesta
    abstencion_correcta  BOOLEAN NOT NULL,      -- acertó al contestar o al decir que no lo cubre
    problemas            TEXT[] NOT NULL,
    resumen              TEXT NOT NULL,         -- una frase, la que ve el usuario
    analisis             TEXT NOT NULL,
    tokens_entrada       INTEGER NOT NULL,      -- 0 si se reusó de la caché
    tokens_salida        INTEGER NOT NULL,
    costo_usd            NUMERIC(10, 6) NOT NULL,
    segundos             REAL NOT NULL,
    desde_cache          BOOLEAN NOT NULL,      -- true = la misma respuesta ya se había auditado
    creada_en            TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE auditorias IS 'Revisión de cada respuesta hecha por Claude contra los fragmentos recuperados';
ALTER TABLE auditorias ADD COLUMN IF NOT EXISTS responde_lo_pedido BOOLEAN;
COMMENT ON COLUMN auditorias.responde_lo_pedido IS 'Hace lo que pidió el usuario, también el nivel de detalle (NULL en auditorías de antes de este campo)';
