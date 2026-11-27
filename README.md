# Agente académico

Agente que responde preguntas sobre un artículo científico, con la sección y la página de donde sale
cada respuesta. Antes de mostrar una respuesta, Claude (API de Anthropic) verifica que salga de los
fragmentos recuperados; si no, el agente la corrige. Tiene un chat en React, un panel `/admin` con
estadísticas, y todo el sistema levanta con un solo `docker compose up`.

**Artículo:** Huang, Y. y Huang, J. X. (2026). *A Survey on Retrieval-Augmented Text Generation for
Large Language Models*. **ACM Computing Surveys** 58(12), artículo 300, pp. 1–38.
DOI [10.1145/3805774](https://doi.org/10.1145/3805774). Licencia
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); el PDF se incluye en
`datos/huang2026-rag-survey.pdf` con esta atribución.

**Cuartil Q1:** según SCImago (SJR 2025), *ACM Computing Surveys* tiene SJR 5.985, es **Q1** y ocupa
el lugar 1 de 403 en *Computer Science (miscellaneous)*
([captura](docs/evidencia/sjr-acm-computing-surveys.png)). 2025 es el año más reciente publicado: el
SJR de 2026 sale a mediados de 2027.

**Integrantes:**

| Nombre | Matrícula |
|---|---|
| Marcelo Cárdenas Leal | A00836049 |
| Fernando Daniel Renteria Saldaña | A00836509 |

---

## Arquitectura

```mermaid
flowchart LR
    U[Navegador<br/>React] -->|HTTPS| N[ngrok]
    N --> A
    U -. local :8080 .-> A
    subgraph app [app · FastAPI]
        A[API + interfaz] --> G[Agente<br/>Pydantic AI]
    end
    G -->|razona y llama tools| O[Ollama<br/>nemotron-3-nano:4b]
    G -->|vector de la pregunta| V[vLLM<br/>Nemotron-3-Embed-1B]
    G -->|búsqueda híbrida| S[(OpenSearch<br/>BM25 + k-NN)]
    A -->|conversaciones, mensajes,<br/>métricas y citas| P[(PostgreSQL)]
    A -->|caché de respuestas<br/>e historial reciente| R[(Redis)]
    G -->|verifica antes<br/>de responder| C[API de Claude<br/>claude-haiku-5-5]
    H[Hugging Face Hub] -. pesos .-> V
    subgraph ingesta [Ingesta · una sola vez]
        PDF[PDF] --> D[Docling] --> F[fragmentos.jsonl]
    end
    F -->|al arrancar, si falta| S
```

Recorrido de una pregunta:

1. El navegador manda la pregunta a `POST /api/chat`, junto con el id de la conversación.
2. La API trae los últimos 3 turnos, primero de Redis y, si no están, de PostgreSQL.
3. El agente de Pydantic AI llama la tool `buscar_en_articulo`. Le pasa la pregunta y su
   traducción al inglés; si la pregunta es de seguimiento («¿y cuál es su desventaja?»), la
   reescribe completa.
4. La tool vectoriza la pregunta con vLLM y hace una búsqueda híbrida en OpenSearch: BM25 sobre el
   inglés y vectores sobre la original. Devuelve los 5 mejores fragmentos y, si la pregunta nombra
   un estudio citado («REALM [48]»), su ficha de la bibliografía.
5. El LLM responde llamando la tool `responder`, con una salida tipada: texto, si el artículo lo
   cubre y qué fragmentos usó.
6. Antes de entregarla, la respuesta pasa por los
   [candados del validador](#candados-antes-de-responder). Tiene que haber buscado, solo puede
   citar fragmentos que la búsqueda le dio, y si contesta debe citar al menos uno. Al final,
   Claude verifica que todo lo que afirma salga de los fragmentos; si no, se la regresa al
   agente para que la corrija.
7. La API convierte los fragmentos citados en sección y páginas, guarda todo en PostgreSQL y
   manda la respuesta por SSE con su veredicto. Mientras tanto, la interfaz muestra en vivo qué se
   está buscando, cuándo Claude verifica y si hubo que corregir.
8. Cada cita es un enlace que abre el PDF del artículo (`/articulo.pdf#page=N`) en la página del
   texto citado. Con «Ver el texto citado» se despliega el texto exacto de cada fragmento en que se
   basó la respuesta (`GET /api/fragmentos`), para comparar la respuesta con su fuente.

**Historial sin login.** Cada navegador recibe un id aleatorio (en su `localStorage`) y la barra
lateral muestra solo sus conversaciones, tituladas con la primera pregunta. Quitar una del
historial la marca como `oculta`: sus mensajes y métricas se conservan para `/admin`.

### Rol de cada componente

| Componente | Rol |
|---|---|
| **Docling** 2.152 | Convierte el PDF conservando secciones, tablas, figuras con su pie y referencias (`ingesta/`) |
| **Hugging Face** | De ahí salen los modelos de embeddings y de Docling, y el tokenizador con que se parten los fragmentos |
| **vLLM** 0.25 | Sirve `nvidia/Nemotron-3-Embed-1B-BF16` (vectores de 2048) para indexar y para cada pregunta |
| **Ollama** 0.40 | Sirve el LLM `nemotron-3-nano:4b`, que razona, llama tools y redacta |
| **OpenSearch** 3.9 | Índice con k-NN (HNSW, Faiss, producto punto) y BM25 (analizador `english`) en los mismos documentos |
| **Pydantic AI** 2.75 | El agente: tools, salida tipada, validación con reintentos y memoria de la conversación |
| **PostgreSQL** 17 | Conversaciones, mensajes, métricas y citas (esquema normalizado) |
| **Redis** 7 | Caché de respuestas e historial reciente de cada conversación |
| **API de Claude** (Anthropic) | Audita cada respuesta y es el juez de la evaluación; corre en la nube, sin usar la RAM ni la GPU de la máquina |
| **React** 19 + Vite | Chat y panel `/admin` |
| **Docker** | 8 servicios (uno solo descarga el LLM y termina) con healthchecks, volúmenes y variables en `.env` |
| **ngrok** | URL pública HTTPS para la demo |

---

## Instalación

Requisitos: Docker con soporte de GPU NVIDIA (en Windows, Docker Desktop con WSL2) y unos 40 GB
libres en disco para imágenes y modelos.

```bash
cp .env.example .env    # y llenar las claves (ver abajo)
docker compose up -d
```

La primera vez, Docker descarga las imágenes y los modelos: el LLM son 2.8 GB y los embeddings
2.3 GB. Después arma el índice a partir de `datos/procesado/fragmentos.jsonl`. Cuando `app` aparece
como `healthy` en `docker compose ps`, todo está listo.

| Variable de `.env` | Para qué |
|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Credenciales de PostgreSQL |
| `ADMIN_TOKEN` | Token para entrar al panel `/admin` |
| `NGROK_AUTHTOKEN` | Token de la cuenta de ngrok ([dónde sacarlo](https://dashboard.ngrok.com/get-started/your-authtoken)) |
| `NGROK_URL` | Opcional: dominio fijo de la cuenta de ngrok (p. ej. `https://algo.ngrok-free.dev`); sin él, la URL cambia en cada arranque |
| `CLAUDE_API_KEY` | Opcional: llave de la [API de Claude](https://console.anthropic.com) para el auditor; sin ella, el chat funciona igual, sin auditoría |
| `MODELO_AUDITOR` | Opcional: modelo que audita (por defecto `claude-haiku-5-5`) |
| `LIMITE_AUDITORIAS_DIA` | Opcional: tope de auditorías nuevas por día (por defecto 200) |

- **Chat:** http://127.0.0.1:8080
- **Panel:** http://127.0.0.1:8080/admin (pide el `ADMIN_TOKEN`)
- **URL pública:** la de `NGROK_URL`, o la que muestra el panel de ngrok en http://127.0.0.1:4040.
  Con la cuenta gratuita, ngrok muestra a cada visitante un aviso la primera vez; se pasa con
  **Visit Site**.

Todos los puertos escuchan solo en `127.0.0.1`. Desde fuera se entra únicamente por ngrok.

### Regenerar la ingesta (opcional)

Los fragmentos ya vienen en el repositorio. Para volver a generarlos desde el PDF (CPU, ~3 minutos,
Python 3.13 con [uv](https://docs.astral.sh/uv/)):

```bash
cd ingesta
uv sync
uv run python convertir.py      # PDF → articulo.json (Docling)
uv run python limpiar.py        # corrige títulos, palomitas, algoritmos y párrafos partidos
uv run python fragmentar.py     # → fragmentos.jsonl (268 fragmentos)
uv run python crear_indice.py --recrear && uv run python indexar.py
```

---

## Hardware y elección de modelos

| | |
|---|---|
| Equipo | ASUS ROG Zephyrus G14, Ryzen 9 270 (8 núcleos), 16 GB de RAM |
| GPU | NVIDIA RTX 5060 Laptop, **8 GB de VRAM** |
| Docker | Docker Desktop sobre WSL2, con tope de 8 GB de RAM |

Con 8 GB de VRAM, los dos modelos tienen que caber juntos en la GPU:

| Modelo | Servidor | VRAM medida | Por qué |
|---|---|---|---|
| `nemotron-3-nano:4b` (Q4_K_M) | Ollama | 3.0 GB | Hace tool calling, tiene contexto largo y cabe junto a los embeddings |
| `Nemotron-3-Embed-1B-BF16` | vLLM | 2.7 GB | Multilingüe: relaciona preguntas en español con el texto en inglés |
| **Total** | | **5.7 de 8.1 GB** | |

Los modelos más grandes sugeridos no caben: el `nemotron-3-nano:30b` pide 18–24 GB y
Qwen2.5-VL-7B en BF16 unos 16 GB. Docling corre en CPU para no competir por la VRAM.

Cada servidor tiene un rol claro:
- **vLLM** sirve los embeddings con el endpoint `/v2/embed`, que pone el prefijo correcto
  (`query:` o `passage:`) según el tipo de texto.
- **Ollama** sirve el LLM cuantizado en GGUF, con el modelo siempre cargado
  (`OLLAMA_KEEP_ALIVE=-1`) para evitar arranques en frío.

---

## Decisiones tomadas con datos

Cada decisión se tomó con un experimento chico y criterios fijados antes de medir. Los scripts
están en `experimentos/` y se pueden volver a correr.

### 1. Tablas con Docling (`experimentos/ingesta/probar_tablas.py`)

Prueba sobre la Tabla 3 (p. 23), de 57 × 13:

| Configuración | Estudios encontrados | Filas pegadas | Filas desalineadas |
|---|---|---|---|
| **TableFormer v1 ACCURATE con cell matching (elegida)** | **59** | 10 | **3** |
| v1 ACCURATE sin cell matching | 52 | 0 | 16 |
| TableFormer v2 | — (todo en una celda) | — | — |

En RAG, perder contenido es peor que tenerlo desordenado.

**Limitación:** la Tabla 3 conserva algunas filas pegadas. Por ejemplo, «REALM [48] kNN-LMs
[82]» queda en un solo renglón.

### 2. Fragmentos (`experimentos/ingesta/probar_fragmentos.py`)

- **512 tokens y tablas en Markdown.** Con Markdown, la Tabla 3 ocupa 8 fragmentos en lugar
  de 20, y cada uno repite el encabezado de columnas. Cinco fragmentos de 512 suman unos 2,500
  tokens, que caben en el contexto de 8,192 del LLM.
- **El chunking respeta la jerarquía.** Se usa el `HybridChunker` de Docling, y cada fragmento
  guarda su ruta de secciones («5 Post-Retrieval > 5.1 Re-Ranking») y sus páginas.
- **La limpieza previa** (`ingesta/limpiar.py`) corrige los problemas que dejó Docling:
  - asigna los niveles de los títulos;
  - recupera las subsecciones 2.1.x y 2.2.x;
  - quita títulos falsos;
  - convierte 265 palomitas ilegibles en ✓;
  - junta en un bloque cada uno de los 4 algoritmos;
  - une 11 párrafos partidos por tablas o por cambios de página.
- Resultado: **268 fragmentos**, 75 de contenido y 193 de bibliografía (una ficha por fragmento).

### 3. Analizador de BM25 (`experimentos/ingesta/probar_analizadores.py`)

| Analizador | Lugar 1 | MRR |
|---|---|---|
| `standard` | 9/15 | 0.702 |
| **`english` (elegido)** | **10/15** | **0.762** |

### 4. Búsqueda híbrida y evidencia de que mejora (`experimentos/ingesta/evaluar_busqueda.py`)

Son 25 preguntas (15 conceptuales y 10 de término exacto), y sus fragmentos correctos se fijaron a
mano **antes** de medir. Se evaluaron en tres condiciones: en inglés, en español, y en español con
la parte BM25 traducida al inglés (`es→en`, que es como trabaja el agente). Cada medición se repitió
5 veces.

| Método (condición `es→en`) | MRR | Recall@5 | Referencias en el top 5 |
|---|---|---|---|
| BM25 | 0.732 | 0.867 | 0.68 |
| Vector | 0.940 | 0.940 | 1.08 |
| Híbrida `min_max` 0.3/0.7 | 0.941 | 0.980 | 0.80 |
| **Híbrida `min_max` 0.3/0.7 sin referencias (elegida)** | **0.943** | **0.980** | **0** |
| Híbrida RRF 0.3/0.7 | 0.917 | 0.960 | 0.80 |

- **La búsqueda híbrida mejora la cobertura** (Recall@5 de 0.940 a 0.980) sin perder precisión en
  el primer lugar. La ganancia viene de preguntas con nombres propios, como RAPTOR o FILCO, que BM25
  encuentra aunque el vector los ubique más abajo.
- Con pesos iguales (0.5/0.5), la híbrida **empeora**: MRR 0.890 en inglés. BM25 sube fragmentos que
  el vector había dejado abajo con razón.
- **Traducir la parte BM25 es indispensable:** con la pregunta en español sin traducir, BM25 da MRR
  0.340.
- Las referencias se filtran porque ocupaban, en promedio, 1 de los 5 lugares que recibe el LLM.
  Las fichas se traen con una tool aparte, por su número de cita.
- Configuración final: **k = 5**. La curva de Recall@k se aplana desde k = 4.

### 5. Quién traduce la pregunta (`experimentos/agente/probar_traduccion.py`)

| Opción | MRR | Recall@5 | Costo |
|---|---|---|---|
| **A. El agente, al llamar la tool (elegida)** | **0.950** | 0.980 | ninguno extra |
| B. Una llamada aparte, solo para traducir | 0.943 | 0.980 | +0.3 s |

La opción A además reescribe las preguntas de seguimiento usando la conversación.

### 6. Cómo pedir la salida tipada (`experimentos/agente/probar_modos_salida.py`)

| Modo de Pydantic AI | Terminan | Buscaron antes de responder | Citas inventadas |
|---|---|---|---|
| `ToolOutput` con nombre de fábrica (`final_result`) | 3/6 | 6/6 | 1/3 |
| `NativeOutput` (JSON forzado) | 6/6 | **0/6** | 1/6 |
| `PromptedOutput` | 6/6 | **0/6** | 1/6 |
| **`ToolOutput` llamada `responder` + instrucción explícita (elegida)** | **6/6** | **6/6** | **0/6** |

Con JSON forzado desde la primera vuelta, el modelo nunca llega a llamar la búsqueda: responde de
memoria e inventa, por ejemplo «REALM es un sistema de gestión de identidad».

### 7. Otros hallazgos

- **Ollama crecía ~450 MB de RAM por pregunta.** `llama-server`, el motor de Ollama, guarda una
  caché de prompts en RAM con tope de 8 GB por defecto, y eso tumbaba a Docker a las ~10
  preguntas. Se limitó a 1 GB con `LLAMA_ARG_CACHE_RAM=1024`: ahora se estabiliza en 2.2 GB.
- **`thinking=False` de Pydantic AI no llega a Ollama** con este modelo. Se manda
  `reasoning_effort: "none"`, y la salida de una respuesta corta bajó de 61 a 3 tokens.
- **Una instrucción dinámica rompía el tool calling** (0 de 9 preguntas buscaban). El idioma de la
  respuesta se indica con una nota al final de la pregunta.

---

## Evaluación del agente (`evaluacion/`)

Son 25 preguntas:
- 15 conceptuales en español y 5 de término exacto en inglés, que el artículo sí responde;
- 5 que el artículo **no** responde.

Los resultados completos, pregunta por pregunta, están en
[`evaluacion/resultados_agente.json`](evaluacion/resultados_agente.json). La corrida de antes de
los [candados](#candados-antes-de-responder) quedó en
[`resultados_agente_sin_candados.json`](evaluacion/resultados_agente_sin_candados.json).

```bash
cd api && uv run --env-file ../.env python ../evaluacion/evaluar_agente.py
```

| Criterio (calculado solo) | Sin candados | **Con candados** |
|---|---|---|
| Buscó en el artículo antes de responder | 25/25 | **25/25** |
| Respondió las que el artículo sí cubre | 20/20 | **20/20** |
| Citó al menos un fragmento correcto (fijado de antemano) | 20/20 | **20/20** |
| Respondió en el idioma de la pregunta | 25/25 | **25/25** |
| Se abstuvo en las que el artículo no cubre | 3/5 | **5/5** |
| Respuestas corregidas antes de mostrarse | — | 5/25 |
| Tiempo promedio por pregunta | 5.3 s | 11.0 s |

El juez es el [auditor](#auditor-con-claude) (`claude-haiku-5-5`). Califica las 25 respuestas,
abstenciones incluidas, con los 5 fragmentos recuperados a la vista:

| Criterio (Claude como juez) | Sin candados | **Con candados** |
|---|---|---|
| Veredicto | 15 aprobadas · 8 con observaciones · 2 reprobadas | **21 aprobadas** · 4 con observaciones · 0 reprobadas |
| Fiel a los fragmentos | 21/25 | **22/25** (3 parcialmente fiel, 0 no fiel) |
| Citas que sostienen la respuesta | 24/25 | **24/25** |
| Acertó al responder o abstenerse | 23/25 | **25/25** |
| Relevancia (1–5) | 4.64 | **4.76** |
| Costo de Claude por corrida | US$0.018 | ~US$0.02 (incluye las verificaciones que regresaron respuestas) |

- **Las 2 abstenciones fallidas se arreglaron con código** (candado 4), sin llamar a Claude. El
  agente decía «el artículo no proporciona los resultados de GPT-7…», pero las marcaba como
  respondibles.
- **Claude regresó 5 respuestas** que tenían afirmaciones sin respaldo. Por ejemplo, una
  describía a RQ-RAG como algo que «simplifica» la consulta, cuando el artículo dice que la
  reescribe, descompone y aclara. Las 5 se corrigieron antes de mostrarse. Las 4 que quedan «con
  observaciones» tienen detalles menores y se muestran con su veredicto.
- **El costo es tiempo.** El promedio sube de 5.3 a 11 s: la verificación agrega ~3 s y cada
  corrección otros ~10 s.
- **Los criterios calculados solos son la medida independiente.** El juez de la segunda tabla es
  el mismo Claude que guió las correcciones, así que esa mejora hay que leerla con cuidado.
- **Sin candados, el juez coincidía con los criterios.** Claude no sabía cuáles preguntas estaban
  fuera del artículo y aun así reprobó justo las 2 abstenciones fallidas.
- **Antes, el juez era el mismo LLM local de 4b.** Solo revisaba 20 respuestas y se juzgaba a sí
  mismo. Con salida JSON forzada calificaba todo como «no» y 1.

---

## Auditor con Claude

Antes de mostrar cada respuesta, el agente le pide a Claude, por la
[API de Anthropic](https://docs.claude.com), que la revise ([`api/auditor.py`](api/auditor.py)).
Claude recibe la pregunta, los **5 fragmentos que recuperó la búsqueda** (marcados como citados o
no) y la respuesta. Con eso devuelve un veredicto con salida estructurada, que el SDK valida contra
un modelo de Pydantic:

| Campo | Qué revisa |
|---|---|
| `fidelidad` | Que todo lo que afirma esté en los fragmentos: `fiel`, `parcialmente_fiel` o `no_fiel` |
| `relevancia` | Qué tanto responde lo que se preguntó (1 a 5) |
| `citas_correctas` | Que los fragmentos citados de verdad sostengan la respuesta |
| `abstencion_correcta` | Que acertara al contestar o al decir que el artículo no lo cubre |
| `veredicto` | `aprobada`, `con_observaciones` o `reprobada`, con la lista de problemas y un resumen |

- **En el chat** aparece como «Aprobada · Verificada por Claude» (o «Con observaciones»,
  «Reprobada»), y se despliega con el detalle. Si hubo que corregir, lo dice. Se guarda en la
  tabla `auditorias` y alimenta el panel `/admin`.
- **Corre en la nube.** No ocupa la RAM ni la GPU de la máquina, donde ya están vLLM y Ollama.
- **Costo.** Usa `claude-haiku-5-5` (US$0.10 por millón de tokens de entrada y US$0.50 de salida).
  Cada auditoría cuesta unos US$0.0007, con ~4,000 tokens de entrada. Se cambia con
  `MODELO_AUDITOR`, y el panel muestra el gasto acumulado. Por qué Haiku y no Sonnet, medido,
  en la tabla de abajo.
- **No paga dos veces.** Una respuesta que sale de la caché de Redis reusa su auditoría. Además, un
  tope diario (`LIMITE_AUDITORIAS_DIA`) protege el saldo si la URL pública recibe muchas preguntas.
- **Si falla, no estorba.** Sin llave, sin saldo o sin red, el chat funciona igual: los candados en
  código siguen activos y la respuesta sale marcada «Sin verificar con Claude».

### Candados antes de responder

Todos viven en el validador de salida de Pydantic AI (`revisar_respuesta` en
[`api/agente.py`](api/agente.py)). Corre cada vez que el modelo intenta entregar su respuesta.
Si un candado falla, Pydantic AI le devuelve el motivo al modelo (`ModelRetry`) y este lo
intenta de nuevo; la respuesta no sale de la API hasta pasarlos.

| # | Candado | Si falla |
|---|---|---|
| 1 | Tiene que haber llamado `buscar_en_articulo` | Reintenta: «primero busca» |
| 2 | Solo puede citar ids que la búsqueda le devolvió | Reintenta con la lista de ids válidos |
| 3 | Si dice que sí puede responder, cita al menos un fragmento | Reintenta; en el último intento se abstiene: **sin cita no hay respuesta** |
| 4 | Si su respuesta solo dice «el artículo no menciona…» | Se toma como abstención (así fallaban las 2 de la evaluación) |
| 5 | Responde en el idioma de la pregunta | Reintenta con la nota de idioma (tras una corrección, una respuesta en inglés salía en español) |
| 6 | Claude verifica la respuesta contra los fragmentos | Si afirma algo sin respaldo, cita mal o debió abstenerse, Claude le regresa la lista de problemas y el agente la corrige (una vez); la versión corregida se vuelve a verificar |

Los candados 1 a 5 son código y no cuestan nada. El 6 cuesta una llamada a Claude, o dos si hubo
corrección. También agrega tiempo: ~3 s cuando la respuesta pasa a la primera y ~10 s más cuando
hay que corregir. Si después de corregir quedan detalles, la respuesta se muestra con su veredicto
(«Con observaciones») en vez de esconderla. Así queda a la vista lo que Claude encontró.

### Haiku contra Sonnet (`evaluacion/comparar_auditores.py`)

Los dos modelos auditaron exactamente los mismos 33 casos, con los mismos fragmentos. 25 son las
respuestas reales de la evaluación de antes de los candados
([`resultados_agente_sin_candados.json`](evaluacion/resultados_agente_sin_candados.json)) y 8 se
echaron a perder a propósito: 4 llevan la respuesta de
otra pregunta y 4 un dato inventado al final («BM25 fue propuesto por Google en 2015…»). Los
resultados completos están en
[`evaluacion/comparacion_auditores.json`](evaluacion/comparacion_auditores.json).

| | `claude-haiku-5-5` | `claude-sonnet-5-5` |
|---|---|---|
| Detecta la respuesta de otra pregunta | **4/4** | **4/4** |
| Detecta el dato inventado | **4/4** | **4/4** |
| Detecta las 2 abstenciones fallidas | **2/2** | **2/2** |
| Sin falsas alarmas de abstención | **23/23** | **23/23** |
| Respuestas reales que considera fieles | 21/25 | 18/25 |
| Costo por auditoría | **US$0.0007** | US$0.0132 |
| Tiempo por auditoría | **3.0 s** | 3.5 s |

- **En los errores conocidos, empatan: 33/33 cada uno.** Coinciden en el veredicto de 22 de las 25
  respuestas reales.
- **Sonnet es más fino.** En las 3 en que no coinciden, Sonnet encontró matices que Haiku dejó
  pasar. Por ejemplo, la respuesta dice que RQ-RAG «simplifica» la consulta, cuando el artículo
  dice que la reescribe, descompone y aclara. O atribuye a CoVe «retroalimentación del sistema de
  recuperación», que el fragmento no menciona. Por eso aprueba menos.
- **Costo × desempeño: gana Haiku.** Detecta todo lo grave por 1/18 del precio. Sonnet vale la
  pena cuando importa el detalle fino, como el reporte de la evaluación, y no en cada pregunta del
  chat.

---

## Persistencia y caché

Esquema de PostgreSQL ([`api/esquema.sql`](api/esquema.sql), con comentarios en cada tabla):

```mermaid
erDiagram
    conversaciones ||--o{ mensajes : tiene
    mensajes ||--o| metricas : "si es del agente"
    mensajes ||--o{ citas : "si es del agente"
    mensajes ||--o| auditorias : "si es del agente"
    conversaciones { uuid id PK  timestamptz creada_en  uuid cliente_id  text titulo  timestamptz actualizada_en  bool oculta }
    mensajes { bigint id PK  uuid conversacion_id FK  text rol  text contenido  timestamptz creado_en }
    metricas { bigint mensaje_id PK  text modelo  int tokens_entrada  int tokens_salida  int llamadas_llm  real segundos_total  real segundos_busqueda  bool se_puede_responder  bool desde_cache  text consulta_en  text_arr tools  text error }
    citas { bigint id PK  bigint mensaje_id FK  text fragmento_id  text seccion  int_arr paginas }
    auditorias { bigint mensaje_id PK  text modelo  text veredicto  text fidelidad  smallint relevancia  bool citas_correctas  bool abstencion_correcta  text_arr problemas  text resumen  text analisis  numeric costo_usd  bool desde_cache }
```

**Redis** tiene dos usos:
- **Caché de respuestas.** Solo para preguntas que abren una conversación: las de seguimiento
  dependen de lo anterior. La llave es la pregunta normalizada más el modelo, y dura 24 h.
  **Efecto medido:** 2 ms con la caché contra 7.4 s sin ella. El panel lo muestra en vivo.
- **Historial reciente** de cada conversación (los últimos 3 turnos), para no consultar PostgreSQL
  en cada pregunta.
- **Auditorías.** Junto a cada respuesta en caché va su auditoría: si la respuesta se repite, no se
  le vuelve a pagar a Claude. También lleva el contador del tope diario de auditorías.

---

## Panel `/admin`

Protegido con `Authorization: Bearer <ADMIN_TOKEN>`. Lee todo de PostgreSQL:
- preguntas y conversaciones;
- tokens de entrada y de salida, totales y por pregunta;
- tiempo promedio, separado en búsqueda y LLM;
- porcentaje de respuestas desde caché, con su latencia comparada;
- preguntas fuera del artículo y errores;
- una gráfica por día;
- la verificación con Claude: porcentaje de respuestas aprobadas, fieles y con citas correctas,
  relevancia promedio, respuestas corregidas, y cuánto se ha gastado en la API (total, promedio
  por respuesta y la más cara). Una respuesta corregida suma el costo de sus dos revisiones;
- la lista de preguntas y respuestas, con buscador, filtro de fechas y el detalle de cada una
  (modelo, llamadas al LLM, tools, consulta a BM25, veredicto y análisis del auditor).

**Costos y desempeño** (`/admin/costos`, botón en el panel). Es una vista resumida para medir
cuánto cuesta y cómo rinde cada conversación:
- **Arriba, los totales del periodo:** gasto en Claude (total, por conversación y por pregunta),
  llamadas a Claude (verificaciones más correcciones), tokens del modelo local y tiempo por
  pregunta.
- **Una fila por conversación:** tokens de entrada → salida y llamadas del modelo local, costo y
  llamadas de Claude, tiempo total y veredictos. Se ordena por más recientes, más caras, más
  tokens o más lentas.
- **Al abrir una conversación**, cada pregunta muestra el modelo local (tokens, llamadas,
  tiempo) y Claude (costo, tokens, cuántas revisiones, tiempo), con su veredicto y si se corrigió.

El modelo local corre en la máquina y no cobra: de él se miden tokens y tiempo. Lo que cuesta
dinero es Claude.

---

## Estructura del repositorio

```
api/            agente (Pydantic AI), búsqueda, auditor (Claude), API de FastAPI, esquema SQL, caché
web/            interfaz en React (chat y /admin)
ingesta/        PDF → fragmentos con Docling, creación del índice
evaluacion/     evaluación del agente (25 preguntas + Claude como juez) y sus resultados
experimentos/   experimentos que respaldan cada decisión (tablas, fragmentos, búsqueda, agente)
datos/          el artículo, los fragmentos y las traducciones de la evaluación
docs/evidencia/ captura del cuartil Q1
pruebas/        peticiones de prueba para Ollama (tool calling) y vLLM (un embedding)
```

## Limitaciones

- La Tabla 3 conserva filas pegadas. Por eso «¿Qué es REALM?» mezcla REALM con kNN-LMs, aunque la
  ficha [48] sí llega al agente.
- El algoritmo 2 se leyó como tabla, y algunos de sus pasos quedan fuera de orden.
- La respuesta llega completa al final, porque el LLM responde con una llamada a tool. Lo que se
  transmite en vivo es el progreso: qué buscó y cuándo está redactando.
- El auditor solo ve los 5 fragmentos recuperados, no el artículo completo: si la búsqueda no trajo
  el fragmento correcto, puede dar por buena una abstención que no lo era.
- La verificación depende de la API de Claude (red y saldo). Si falla, los candados en código
  siguen activos y la respuesta sale con el aviso «Sin verificar con Claude».
- Verificar antes de mostrar duplica el tiempo promedio (de 5.3 a 11 s). Solo se corrige una vez:
  si después de corregir quedan detalles, se muestran en el veredicto en vez de volver a intentar.
