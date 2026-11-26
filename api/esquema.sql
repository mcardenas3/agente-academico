-- Esquema de PostgreSQL del agente académico. La API lo aplica al arrancar; "IF NOT EXISTS"
-- hace que correrlo varias veces no cambie nada.
--
--   conversaciones 1 ── n mensajes 1 ── 0..1 metricas
--                                  1 ── n citas
--
-- Cada pregunta del usuario y cada respuesta del agente es una fila de "mensajes". Las métricas y
-- las citas cuelgan solo de los mensajes del agente.

CREATE TABLE IF NOT EXISTS conversaciones (
    id          UUID PRIMARY KEY,
    creada_en   TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE conversaciones IS 'Una conversación del chat; el navegador guarda su id';

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

CREATE TABLE IF NOT EXISTS citas (
    id           BIGSERIAL PRIMARY KEY,
    mensaje_id   BIGINT NOT NULL REFERENCES mensajes (id) ON DELETE CASCADE,
    fragmento_id TEXT NOT NULL,                 -- id del fragmento en OpenSearch
    seccion      TEXT NOT NULL,
    paginas      INTEGER[] NOT NULL
);
COMMENT ON TABLE citas IS 'Fragmentos del artículo en que se basa cada respuesta';
CREATE INDEX IF NOT EXISTS citas_por_mensaje ON citas (mensaje_id);
