"""Evaluación de la búsqueda: ¿en qué lugar sale el fragmento correcto con cada método?

Uso (desde la carpeta ingesta, con opensearch y vllm encendidos):
    uv run python ../experimentos/ingesta/evaluar_busqueda.py

Hace 25 preguntas con cada método de búsqueda en tres condiciones: en inglés, en español y en
español con la parte BM25 traducida al inglés (las traducciones las genera
traducir_preguntas.py). Anota en qué lugar del top 10 sale el primer fragmento que responde cada
pregunta y resume por condición y por clase de pregunta con Top 1, Top 5, Top 10, MRR y Recall@5.
Todo se repite REPETICIONES veces y se reporta el rango, porque los vectores varían un poco.
"""

import json
import sys
from pathlib import Path

import httpx
from opensearchpy import OpenSearch

# Los scripts de la ingesta viven en ingesta/: se agrega esa carpeta a donde Python busca módulos
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ingesta"))
from crear_indice import FRAGMENTOS, INDICE, URL_OPENSEARCH  # noqa: E402
from indexar import MODELO, URL_EMBED  # noqa: E402

# Cuántos resultados se revisan por pregunta
TOP = 10
# Cuántas veces se repite la evaluación completa (ver main: los vectores varían un poco)
REPETICIONES = 5
# Inicio del id de cada fragmento; le sigue su número con tres dígitos
PREFIJO_ID = "huang2026-rag-survey-"
# Traducciones al inglés de las preguntas en español: {español: inglés}. Las escribe
# traducir_preguntas.py con el LLM; se guardan para que la evaluación sea repetible
TRADUCCIONES = Path(__file__).resolve().parents[2] / "datos" / "evaluacion" / "traducciones.json"

# Preguntas conceptuales: (inglés, español, fragmentos que la responden). Son las 15 de
# probar_analizadores.py; los fragmentos correctos se fijaron a mano antes de aquel experimento
CONCEPTUALES = [
    ("Why do large language models hallucinate and how does RAG help?",
     "¿Por qué alucinan los modelos de lenguaje y cómo ayuda RAG?", [5]),
    ("What are the phases of the RAG paradigm?",
     "¿Cuáles son las fases del paradigma RAG?", [10]),
    ("How do BM25 and retrievers based on pretrained language models differ?",
     "¿En qué se diferencian BM25 y los recuperadores basados en modelos de lenguaje?", [8]),
    ("How does locality-sensitive hashing find nearest neighbors?",
     "¿Cómo funciona el hashing sensible a la localidad para buscar vecinos cercanos?", [17]),
    ("What strategies exist for modifying the user's query?",
     "¿Qué estrategias existen para modificar la consulta del usuario?", [12, 20]),
    ("What is query reformulation and which methods use it?",
     "¿Qué es la reformulación de consultas y qué métodos la usan?", [21]),
    ("What is the difference between iterative and recursive retrieval?",
     "¿Qué diferencia hay entre la recuperación iterativa y la recursiva?", [25, 26, 27]),
    ("In adaptive retrieval, how does the model decide when to retrieve more information?",
     "En la recuperación adaptativa, ¿cómo decide el modelo cuándo recuperar más información?",
     [28, 32]),
    ("How do supervised re-ranking models work?",
     "¿Cómo funcionan los modelos supervisados de reordenamiento?", [34]),
    ("Why filter the retrieved documents after retrieval?",
     "¿Para qué sirve filtrar los documentos recuperados?", [36, 37]),
    ("How are several retrieved documents combined when generating the answer?",
     "¿Cómo se combinan varios documentos recuperados al generar la respuesta?", [39]),
    ("How are responses personalized for each user?",
     "¿Cómo se personalizan las respuestas para cada usuario?", [41, 42]),
    ("Which metrics and datasets are used to evaluate RAG?",
     "¿Qué métricas y conjuntos de datos se usan para evaluar RAG?", [43]),
    ("How can the context be compressed to speed up generation?",
     "¿Cómo se comprime el contexto para que la generación sea más rápida?", [69]),
    ("Do more documents in a long context always improve the answer?",
     "¿Más documentos en un contexto largo siempre mejoran la respuesta?", [71]),
]

# Preguntas de término exacto: nombran un método que aparece en muy pocos fragmentos. Solo cuentan
# los fragmentos que lo explican; un renglón de tabla o una referencia que solo lo nombra, no
TERMINOS = [
    ("What is HyDE and how does it improve retrieval?",
     "¿Qué es HyDE y cómo mejora la recuperación?", [66]),
    ("How does RAPTOR organize documents for retrieval?",
     "¿Cómo organiza RAPTOR los documentos para recuperarlos?", [27]),
    ("Which strategies does FILCO use to filter context?",
     "¿Qué estrategias usa FILCO para filtrar el contexto?", [37]),
    ("How does the Chain-of-Verification (CoVe) method work?",
     "¿Cómo funciona el método Chain-of-Verification (CoVe)?", [40]),
    ("What do the RAGAS and ARES frameworks measure?",
     "¿Qué miden los frameworks RAGAS y ARES?", [44, 47]),
    ("What is the SELF-ROUTE method?",
     "¿Qué es el método SELF-ROUTE?", [73]),
    ("What is Order-Preserve RAG (OP-RAG)?",
     "¿Qué es Order-Preserve RAG (OP-RAG)?", [72]),
    ("How does RankLLaMA compute relevance?",
     "¿Cómo calcula RankLLaMA la relevancia?", [34]),
    ("What does Step-Back prompting do before retrieval?",
     "¿Qué hace Step-Back prompting antes de recuperar?", [22]),
    ("How does GENREAD use the model's own knowledge?",
     "¿Cómo usa GENREAD el conocimiento del propio modelo?", [18]),
]

# Todas juntas, cada una con su clase: (inglés, español, relevantes, clase)
PREGUNTAS = ([(*p, "conceptual") for p in CONCEPTUALES]
             + [(*p, "término") for p in TERMINOS])

# Condiciones de la evaluación: nombre → (texto que recibe BM25, idioma de la pregunta que se
# vectoriza). En "es→en" BM25 busca con la traducción y el vector se queda con el español, como
# hará el agente: el vector ya entiende español y BM25 necesita las palabras del artículo
CONDICIONES = {
    "en": ("inglés", "en"),
    "es": ("español", "es"),
    "es→en": ("traducción", "es"),
}


# --- Métodos de búsqueda: cada uno recibe el texto para BM25 y el vector de la pregunta y devuelve
# --- el cuerpo de la búsqueda para OpenSearch. Agregar un método es agregar una función a METODOS.

def cuerpo_bm25(texto: str, vector: list[float]) -> dict:
    """Por palabras: BM25 sobre texto_para_buscar (analizador english). No usa el vector."""
    return {"query": {"match": {"texto_para_buscar": texto}}}


def cuerpo_vector(texto: str, vector: list[float]) -> dict:
    """Por significado: los TOP vectores más parecidos al de la pregunta. No usa el texto."""
    return {"query": {"knn": {"vector": {"vector": vector, "k": TOP}}}}


# --- Búsqueda híbrida: OpenSearch corre las dos búsquedas (BM25 y vector) dentro de una consulta
# --- "hybrid" y un procesador del search pipeline junta las dos listas en una sola.

def min_max(peso_bm25: float) -> dict:
    """Procesador por puntajes: lleva los puntajes de cada lista a 0–1 y los promedia con pesos.

    El mejor de cada lista queda en 1 y el peor en 0; un fragmento que solo salió en una de las
    dos listas recibe 0 en la otra. El peso del vector es lo que le falta al de BM25 para sumar 1.
    """
    return {"normalization-processor": {
        "normalization": {"technique": "min_max"},
        "combination": {"technique": "arithmetic_mean",
                        "parameters": {"weights": [peso_bm25, round(1 - peso_bm25, 2)]}},
    }}


def rrf(peso_bm25: float) -> dict:
    """Procesador por lugares (Reciprocal Rank Fusion): ignora los puntajes y suma 1/(60 + lugar)
    de cada lista, con pesos. Premia a los fragmentos que salen arriba en las dos."""
    return {"score-ranker-processor": {
        "combination": {"technique": "rrf", "rank_constant": 60,
                        "parameters": {"weights": [peso_bm25, round(1 - peso_bm25, 2)]}},
    }}


def hibrida(procesador: dict, filtro: dict | None = None):
    """Arma un método híbrido: las mismas dos búsquedas de arriba, combinadas con el procesador.

    El pipeline va dentro del cuerpo de la búsqueda, así que no se crea nada en OpenSearch. Si
    hay filtro, se aplica a las dos subconsultas antes de combinarlas.
    """
    def armar_cuerpo(texto: str, vector: list[float]) -> dict:
        consulta = {"queries": [
            cuerpo_bm25(texto, vector)["query"],    # subconsulta 1: por palabras
            cuerpo_vector(texto, vector)["query"],  # subconsulta 2: por significado
        ]}
        if filtro:
            consulta["filter"] = filtro
        return {
            "query": {"hybrid": consulta},
            "search_pipeline": {"phase_results_processors": [procesador]},
        }
    return armar_cuerpo


# Filtro para dejar fuera las 193 entradas de la bibliografía: solo fragmentos de contenido
SOLO_CONTENIDO = {"term": {"tipo": "contenido"}}

# Métodos a comparar. En los híbridos, el número es el peso de BM25 (el resto es del vector):
# 50 = pesos iguales; 30 = más peso al vector (el ejemplo de la documentación de OpenSearch).
# Se fijaron antes de medir y no se ajustan después, para no acomodarlos a estas 25 preguntas.
# MM30f es MM30 (el elegido en el paso 3) sin referencias, para decidir si se filtran
METODOS = {
    "BM25": cuerpo_bm25,
    "Vector": cuerpo_vector,
    "MM50": hibrida(min_max(0.5)),
    "MM30": hibrida(min_max(0.3)),
    "RRF50": hibrida(rrf(0.5)),
    "RRF30": hibrida(rrf(0.3)),
    "MM30f": hibrida(min_max(0.3), filtro=SOLO_CONTENIDO),
}
# Métodos para la tabla de Recall@k (paso 4: cuántos fragmentos recibe el LLM)
METODOS_TOP_K = ["Vector", "MM30", "MM30f"]


def vectorizar_preguntas(textos: list[str]) -> list[list[float]]:
    """Le pide a vLLM los vectores de todas las preguntas en una sola llamada ("query: ")."""
    respuesta = httpx.post(URL_EMBED, timeout=120, json={
        "model": MODELO,
        "input_type": "query",
        "texts": textos,
        "embedding_types": ["float"],
        "truncate": "NONE",
    })
    respuesta.raise_for_status()
    return respuesta.json()["embeddings"]["float"]


def buscar(cliente: OpenSearch, cuerpo: dict) -> list[int]:
    """Corre la búsqueda y devuelve los números de los fragmentos encontrados, en orden."""
    # Solo interesan los ids, así que no se pide el texto de los fragmentos
    respuesta = cliente.search(index=INDICE, body={"size": TOP, "_source": False, **cuerpo})
    # El número va al final del id: "huang2026-rag-survey-034" → 34
    return [int(r["_id"].removeprefix(PREFIJO_ID)) for r in respuesta["hits"]["hits"]]


def lugar_del_relevante(encontrados: list[int], relevantes: list[int]) -> int | None:
    """Lugar del primer fragmento correcto en los resultados (None si no salió)."""
    for lugar, numero in enumerate(encontrados, start=1):
        if numero in relevantes:
            return lugar
    return None


def recall_k(encontrados: list[int], relevantes: list[int], k: int) -> float:
    """Qué fracción de TODOS los fragmentos correctos entró en los k primeros resultados.

    Con un solo correcto vale 1 o 0; con tres correctos y dos en el top k, vale 0.67.
    """
    return len(set(encontrados[:k]) & set(relevantes)) / len(relevantes)


def recall_5(encontrados: list[int], relevantes: list[int]) -> float:
    """Recall@5: la cobertura con los 5 fragmentos que se piensa pasar al LLM."""
    return recall_k(encontrados, relevantes, 5)


def metricas(resultados: list[list[int]], relevantes: list[list[int]]
             ) -> tuple[int, int, int, float, float]:
    """Top 1, Top 5 y Top 10 (cuántas preguntas encontraron un fragmento correcto ahí), MRR y
    Recall@5 promedio.

    MRR es el promedio de 1/lugar: vale 1 si siempre sale primero, 0.5 si siempre sale
    segundo, y una pregunta que no encontró nada suma 0.
    """
    lugares = [lugar_del_relevante(e, r) for e, r in zip(resultados, relevantes)]
    encontrados = [lugar for lugar in lugares if lugar]
    top1 = sum(lugar == 1 for lugar in encontrados)
    top5 = sum(lugar <= 5 for lugar in encontrados)
    mrr = sum(1 / lugar for lugar in encontrados) / len(lugares)
    recall = sum(recall_5(e, r) for e, r in zip(resultados, relevantes)) / len(resultados)
    return top1, top5, len(encontrados), mrr, recall


def correr_una_vez(cliente: OpenSearch, textos: dict[str, list[str]]) -> dict:
    """Vectoriza las preguntas y corre todas las búsquedas una vez.

    Devuelve resultados[(condición, método)] = fragmentos encontrados (en orden) por pregunta.
    """
    # Los 50 vectores (25 en inglés y 25 en español) en una sola llamada a vLLM;
    # vectores[idioma][número de pregunta]
    n = len(PREGUNTAS)
    todos = vectorizar_preguntas(textos["inglés"] + textos["español"])
    vectores = {"en": todos[:n], "es": todos[n:]}

    resultados = {}
    for condicion, (texto_bm25, idioma_vector) in CONDICIONES.items():
        for metodo, armar_cuerpo in METODOS.items():
            resultados[(condicion, metodo)] = [
                buscar(cliente, armar_cuerpo(textos[texto_bm25][k], vectores[idioma_vector][k]))
                for k in range(n)
            ]
    return resultados


def rango(valores: list, formato: str) -> str:
    """Un solo valor si no cambió entre repeticiones; si cambió, "mínimo–máximo"."""
    menor, mayor = min(valores), max(valores)
    if menor == mayor:
        return format(menor, formato)
    return f"{menor:{formato}}–{mayor:{formato}}"


def main() -> None:
    if not TRADUCCIONES.exists():
        raise SystemExit(f"No existe {TRADUCCIONES}; córrelo primero: "
                         "uv run python ../experimentos/ingesta/traducir_preguntas.py")
    traducciones = json.loads(TRADUCCIONES.read_text(encoding="utf-8"))
    cliente = OpenSearch(hosts=[URL_OPENSEARCH])

    # Los tres textos de cada pregunta: textos[clave][número de pregunta]
    textos = {
        "inglés": [p[0] for p in PREGUNTAS],
        "español": [p[1] for p in PREGUNTAS],
        "traducción": [traducciones[p[1]] for p in PREGUNTAS],
    }
    n = len(PREGUNTAS)

    # Se repite todo, incluida la vectorización: vLLM no da vectores idénticos en cada llamada
    # (GPU con BF16), y en los casi empates eso basta para cambiar el orden. Repetir muestra qué
    # diferencias entre métodos se sostienen y cuáles son ruido
    corridas = [correr_una_vez(cliente, textos) for _ in range(REPETICIONES)]

    # Una tabla por condición: lugar del primer fragmento correcto ("-" = no salió en el top 10).
    # Si cambió entre repeticiones, se muestran los lugares que salieron ("2/3"). La pregunta que
    # se muestra es la que recibió BM25
    for condicion, (texto_bm25, _) in CONDICIONES.items():
        print(f"{'#':>2}  {'Pregunta (' + condicion + ')':<52}"
              + "".join(f"{m:>8}" for m in METODOS))
        for k, p in enumerate(PREGUNTAS):
            celdas = ""
            for m in METODOS:
                lugares = {lugar_del_relevante(c[(condicion, m)][k], p[2]) for c in corridas}
                # Orden: primero los números, al final "-" (no salió)
                texto = "/".join(str(x) if x else "-" for x in sorted(lugares, key=lambda x: x or 99))
                celdas += f"{texto:>8}"
            print(f"{k + 1:>2}  {textos[texto_bm25][k][:50]:<52}{celdas}")
        print()

    # Resumen por idioma y por clase de pregunta. "varios correctos" junta las preguntas con más
    # de un fragmento correcto: solo en ellas Recall@5 dice algo que Top 5 no dice ya
    clases = {
        "conceptual": [k for k, p in enumerate(PREGUNTAS) if p[3] == "conceptual"],
        "término": [k for k, p in enumerate(PREGUNTAS) if p[3] == "término"],
        "varios correctos": [k for k, p in enumerate(PREGUNTAS) if len(p[2]) > 1],
        "todas": list(range(n)),
    }
    # Cada métrica se calcula en cada repetición; si cambió, se muestra "mínimo–máximo"
    print(f"Métricas de {REPETICIONES} repeticiones (\"a–b\" = mínimo–máximo)\n")
    print(f"{'Cond.':<7}{'Clase':<22}{'Método':<8}{'Top 1':>8}{'Top 5':>8}{'Top 10':>8}"
          f"{'MRR':>14}{'Rec@5':>14}")
    for condicion in CONDICIONES:
        for clase, indices in clases.items():
            for metodo in METODOS:
                # por_corrida[r] = (top1, top5, top10, mrr, recall) de la repetición r
                por_corrida = [
                    metricas([c[(condicion, metodo)][k] for k in indices],
                             [PREGUNTAS[k][2] for k in indices])
                    for c in corridas
                ]
                # zip(*...) junta cada métrica de todas las repeticiones: top1s, top5s, ...
                top1, top5, top10, mrr, recall = zip(*por_corrida)
                print(f"{condicion:<7}{f'{clase} ({len(indices)})':<22}{metodo:<8}"
                      f"{rango(top1, 'd'):>8}{rango(top5, 'd'):>8}{rango(top10, 'd'):>8}"
                      f"{rango(mrr, '.3f'):>14}{rango(recall, '.3f'):>14}")
            print()

    # --- Paso 4: cuántos fragmentos pasarle al LLM y si conviene quitar las referencias ---

    # Recall@k para k = 1…10: qué fracción de los fragmentos correctos ya entró en los k
    # primeros. Se busca el k más chico a partir del cual la curva ya no sube
    print(f"Recall@k, todas las preguntas ({n})\n")
    print(f"{'Cond.':<7}{'Método':<8}" + "".join(f"{f'k={k}':>13}" for k in range(1, TOP + 1)))
    for condicion in CONDICIONES:
        for metodo in METODOS_TOP_K:
            celdas = ""
            for k in range(1, TOP + 1):
                valores = [
                    sum(recall_k(c[(condicion, metodo)][i], p[2], k)
                        for i, p in enumerate(PREGUNTAS)) / n
                    for c in corridas
                ]
                celdas += f"{rango(valores, '.3f'):>13}"
            print(f"{condicion:<7}{metodo:<8}{celdas}")
        print()

    # Cuántas entradas de la bibliografía ocupan lugares del top 5: lugares que podría usar un
    # fragmento de contenido. El tipo de cada fragmento se lee de fragmentos.jsonl
    with FRAGMENTOS.open(encoding="utf-8") as archivo:
        tipos = {int(f["id"].removeprefix(PREFIJO_ID)): f["tipo"]
                 for f in map(json.loads, archivo)}
    print("Referencias en el top 5 (promedio por pregunta; preguntas con al menos una)\n")
    print(f"{'Cond.':<7}{'Método':<8}{'Promedio':>14}{'Preguntas':>12}")
    for condicion in CONDICIONES:
        for metodo in METODOS:
            promedios, con_alguna = [], []
            for c in corridas:
                cuantas = [sum(tipos[x] == "referencia" for x in c[(condicion, metodo)][i][:5])
                           for i in range(n)]
                promedios.append(sum(cuantas) / n)
                con_alguna.append(sum(x > 0 for x in cuantas))
            print(f"{condicion:<7}{metodo:<8}{rango(promedios, '.2f'):>14}"
                  f"{rango(con_alguna, 'd'):>12}")
        print()


if __name__ == "__main__":
    main()
