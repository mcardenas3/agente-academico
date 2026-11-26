-- Esquema de PostgreSQL del agente académico. La API lo aplica al arrancar; "IF NOT EXISTS"
-- hace que correrlo varias veces no cambie nada.
--
--   conversaciones 1 ── n mensajes 1 ── 0..1 metricas
--                                  1 ── n citas
--
-- Cada pregunta del usuario y cada respuesta del agente es una fila de "mensajes". Las métricas y
-- las citas cuelgan solo de los mensajes del agente.

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

-- Biblioteca de artículos: el original y los que se suben desde /admin. Un artículo subido pasa por
-- en_cola → procesando → listo (o error); lo procesa el servicio "trabajador" (ingesta/trabajador.py)
CREATE TABLE IF NOT EXISTS articulos (
    id            TEXT PRIMARY KEY,                 -- prefijo de los ids de sus fragmentos
    titulo        TEXT NOT NULL,
    archivo       TEXT NOT NULL,                    -- nombre del PDF guardado
    original      BOOLEAN NOT NULL DEFAULT false,   -- el survey del proyecto: no se puede borrar
    estado        TEXT NOT NULL CHECK (estado IN ('en_cola', 'procesando', 'listo', 'error')),
    progreso      TEXT,                             -- lo que está haciendo el trabajador ahora
    error         TEXT,
    paginas       INTEGER,
    fragmentos    INTEGER,
    subido_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    procesado_en  TIMESTAMPTZ
);
COMMENT ON TABLE articulos IS 'Artículos que el agente puede consultar; sus fragmentos viven en OpenSearch';
INSERT INTO articulos (id, titulo, archivo, original, estado, paginas, fragmentos, procesado_en)
VALUES ('huang2026-rag-survey',
        'A Survey on Retrieval-Augmented Text Generation for Large Language Models',
        'huang2026-rag-survey.pdf', true, 'listo', 38, 268, now())
ON CONFLICT (id) DO NOTHING;

-- Artículo sobre el que pregunta cada conversación; NULL = todos los de la biblioteca. Sin FK a
-- propósito: borrar un artículo no debe borrar las conversaciones que lo usaron
ALTER TABLE conversaciones ADD COLUMN IF NOT EXISTS articulo_id TEXT;
COMMENT ON COLUMN conversaciones.articulo_id IS 'Artículo elegido al empezar; NULL = todos';

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
