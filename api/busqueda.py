"""Búsquedas sobre el artículo que usarán las tools del agente.

1. Búsqueda híbrida, la configuración que ganó en la Etapa 4 (MM30f):
   - BM25 con la pregunta en inglés (el artículo está en inglés y BM25 compara palabras);
   - vector con la pregunta original (el modelo de embeddings entiende español e inglés);
   - las dos listas se juntan con min_max y pesos 0.3 (BM25) / 0.7 (vector);
   - solo fragmentos de contenido (sin las 193 entradas de la bibliografía);
   - se le pasan K = 5 fragmentos al LLM.
2. Fichas de la bibliografía por su número de cita ("[48]"), porque la búsqueda deja fuera las
   referencias y una ficha no siempre trae el nombre corto del método (la [48] es REALM y no dice
   "REALM").
"""

import httpx
from opensearchpy import OpenSearch
from pydantic import BaseModel

# 127.0.0.1 y no "localhost": en Windows, Python prueba primero ::1 (IPv6), donde Docker no
# escucha, y espera todo el timeout antes de pasar a 127.0.0.1
URL_OPENSEARCH = "http://127.0.0.1:9200"
URL_EMBED = "http://127.0.0.1:8000/v2/embed"
# Índice donde la ingesta guardó los fragmentos
INDICE = "fragmentos"
# Modelo de embeddings que sirve vLLM (el mismo con el que se indexaron los fragmentos)
MODELO = "nvidia/Nemotron-3-Embed-1B-BF16"

# Cuántos fragmentos se le pasan al LLM (decisión de la Etapa 4, paso 4)
K = 5
# Cuántos candidatos trae cada búsqueda antes de combinar. Es el mismo 10 de la evaluación:
# min_max lleva a 0–1 los puntajes de los candidatos de cada lista, así que con otro número
# cambiaría la normalización y el orden ya no sería el que se midió
CANDIDATOS = 10
# Peso de BM25 en la mezcla; el del vector es lo que falta para sumar 1
PESO_BM25 = 0.3
# Nombre con que se guarda el pipeline en OpenSearch (se crea una vez, ver preparar_pipeline)
PIPELINE = "hibrida-mm30"
# Solo fragmentos de contenido: las fichas de la bibliografía se traen con otra tool por su número
SOLO_CONTENIDO = {"term": {"tipo": "contenido"}}
# Campos que se piden de cada fragmento: lo que el agente necesita para responder y citar
CAMPOS = ["id", "seccion", "paginas", "texto"]


class Fragmento(BaseModel):
    """Un pedazo del artículo encontrado por la búsqueda, con lo necesario para citarlo."""

    id: str              # "huang2026-rag-survey-034"
    seccion: str         # "5 Post-Retrieval > 5.1 Re-Ranking"
    paginas: list[int]   # [16, 17]
    texto: str           # el texto del fragmento, tal como lo leerá el LLM


def preparar_pipeline(cliente: OpenSearch) -> None:
    """Crea (o reemplaza) en OpenSearch el pipeline que junta las dos listas de resultados.

    En la evaluación iba dentro de cada búsqueda; aquí se guarda una vez con nombre y cada
    búsqueda solo lo menciona. Correrlo dos veces no duplica nada: PUT reemplaza.
    """
    cliente.search_pipeline.put(id=PIPELINE, body={
        "description": "Búsqueda híbrida: min_max, 0.3 BM25 / 0.7 vector",
        "phase_results_processors": [{"normalization-processor": {
            "normalization": {"technique": "min_max"},
            "combination": {"technique": "arithmetic_mean",
                            "parameters": {"weights": [PESO_BM25, round(1 - PESO_BM25, 2)]}},
        }}],
    })


def vectorizar(pregunta: str) -> list[float]:
    """Le pide a vLLM el vector de la pregunta. Con input_type "query", vLLM le pone el prefijo
    "query: " que espera el modelo (los fragmentos se vectorizaron como "passage: ")."""
    respuesta = httpx.post(URL_EMBED, timeout=30, json={
        "model": MODELO,
        "input_type": "query",
        "texts": [pregunta],
        "embedding_types": ["float"],
        "truncate": "NONE",  # si la pregunta fuera demasiado larga, error en vez de cortarla
    })
    respuesta.raise_for_status()  # si vLLM contesta con error, se detiene aquí
    return respuesta.json()["embeddings"]["float"][0]


def buscar_con_vector(cliente: OpenSearch, pregunta_en: str, vector: list[float]) -> list[Fragmento]:
    """La búsqueda híbrida con el vector ya calculado. Devuelve los K mejores fragmentos.

    Va separada de vectorizar() para poder comparar contra la evaluación con el mismo vector.
    """
    cuerpo = {
        "size": CANDIDATOS,  # se piden 10 como en la evaluación y abajo se quedan los K primeros
        "_source": CAMPOS,
        "query": {"hybrid": {
            "queries": [
                # Subconsulta 1, por palabras: BM25 sobre títulos + texto, con la pregunta en inglés
                {"match": {"texto_para_buscar": pregunta_en}},
                # Subconsulta 2, por significado: los vectores más parecidos al de la pregunta
                {"knn": {"vector": {"vector": vector, "k": CANDIDATOS}}},
            ],
            # El filtro se aplica a las dos subconsultas antes de combinarlas
            "filter": SOLO_CONTENIDO,
        }},
    }
    respuesta = cliente.search(index=INDICE, body=cuerpo, params={"search_pipeline": PIPELINE})
    resultados = respuesta["hits"]["hits"][:K]
    # Cada resultado trae sus campos en "_source"; Pydantic revisa que tengan el tipo correcto
    return [Fragmento(**r["_source"]) for r in resultados]


def buscar_fragmentos(cliente: OpenSearch, pregunta: str, pregunta_en: str) -> list[Fragmento]:
    """Lo que hará la tool: vectoriza la pregunta original y busca con las dos versiones."""
    return buscar_con_vector(cliente, pregunta_en, vectorizar(pregunta))


# --- Fichas de la bibliografía ---

# Cuántas fichas se pueden pedir de una vez: alcanza para citas como "[48, 82, 101]" y evita que
# el LLM pida media bibliografía y se llene su contexto
MAX_REFERENCIAS = 10


class Referencia(BaseModel):
    """Una ficha de la bibliografía del artículo."""

    numero: int          # 48, el que aparece en el texto como "[48]"
    texto: str           # autores, año, título y dónde se publicó
    paginas: list[int]   # página del artículo donde está la ficha


class ResultadoReferencias(BaseModel):
    """Las fichas encontradas y los números que no existen en la bibliografía."""

    referencias: list[Referencia]
    no_encontradas: list[int]  # así el agente sabe que el número estaba mal, en vez de inventar


def traer_referencias(cliente: OpenSearch, numeros: list[int]) -> ResultadoReferencias:
    """Trae las fichas de la bibliografía con esos números de cita, en el orden pedido."""
    # Sin repetidos y conservando el orden: dict.fromkeys guarda la primera aparición de cada uno
    numeros = list(dict.fromkeys(numeros))
    if not numeros:
        raise ValueError("Hay que pedir al menos un número de referencia.")
    if len(numeros) > MAX_REFERENCIAS:
        raise ValueError(f"Se pueden pedir hasta {MAX_REFERENCIAS} referencias a la vez.")

    # Búsqueda exacta, no por parecido: "terms" pide los documentos cuyo numero_referencia esté
    # en la lista. Va en "filter" porque no hace falta puntaje, solo saber si coincide
    cuerpo = {
        "size": len(numeros),
        "_source": ["numero_referencia", "texto", "paginas"],
        "query": {"bool": {"filter": [
            {"term": {"tipo": "referencia"}},
            {"terms": {"numero_referencia": numeros}},
        ]}},
    }
    respuesta = cliente.search(index=INDICE, body=cuerpo)
    # Por número, para devolverlas en el orden en que se pidieron
    por_numero = {
        r["_source"]["numero_referencia"]: Referencia(
            numero=r["_source"]["numero_referencia"],
            texto=r["_source"]["texto"],
            paginas=r["_source"]["paginas"],
        )
        for r in respuesta["hits"]["hits"]
    }
    return ResultadoReferencias(
        referencias=[por_numero[n] for n in numeros if n in por_numero],
        no_encontradas=[n for n in numeros if n not in por_numero],
    )
