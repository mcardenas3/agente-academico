"""Prueba rápida del índice: busca una pregunta por palabras (BM25) y por significado (vector).

Uso (desde la carpeta ingesta, con opensearch y vllm encendidos):
    uv run python probar_busqueda.py "How do supervised re-ranking models work?"

Muestra los 5 primeros resultados de tres búsquedas: por palabras, por significado y por
significado sin las referencias. Sirve para revisar a ojo que el índice responde con sentido;
la comparación con números es de la Etapa 4.
"""

import sys

import httpx
from opensearchpy import OpenSearch

from crear_indice import INDICE, URL_OPENSEARCH
from indexar import MODELO, URL_EMBED

# Cuántos resultados se muestran por búsqueda
TOP = 5
# Pregunta que se usa si no se escribe ninguna al correr el script
PREGUNTA_EJEMPLO = "How do supervised re-ranking models work?"
# Campos que se piden de cada resultado (el texto y el vector no hacen falta para revisar)
CAMPOS = ["id", "tipo", "seccion", "paginas"]


def vector_de_pregunta(pregunta: str) -> list[float]:
    """Le pide a vLLM el vector de la pregunta, tratada como consulta ("query: ")."""
    respuesta = httpx.post(URL_EMBED, timeout=60, json={
        "model": MODELO,
        "input_type": "query",
        "texts": [pregunta],
        "embedding_types": ["float"],
    })
    respuesta.raise_for_status()
    return respuesta.json()["embeddings"]["float"][0]


def mostrar(titulo: str, respuesta: dict) -> None:
    """Imprime los resultados: lugar, puntaje, número de fragmento, tipo, páginas y sección."""
    print(f"\n{titulo}")
    for lugar, resultado in enumerate(respuesta["hits"]["hits"], start=1):
        f = resultado["_source"]
        paginas = ",".join(str(p) for p in f["paginas"])
        print(f"  {lugar}. {resultado['_score']:6.3f}  {f['id'][-3:]}  {f['tipo']:<10} "
              f"p.{paginas:<6} {f['seccion'][:55]}")


def main() -> None:
    pregunta = sys.argv[1] if len(sys.argv) > 1 else PREGUNTA_EJEMPLO
    cliente = OpenSearch(hosts=[URL_OPENSEARCH])
    print(f"Pregunta: {pregunta}")

    # 1. Por palabras: BM25 sobre texto_para_buscar (con el analizador english)
    por_palabras = cliente.search(index=INDICE, body={
        "size": TOP, "_source": CAMPOS,
        "query": {"match": {"texto_para_buscar": pregunta}},
    })
    mostrar("Por palabras (BM25):", por_palabras)

    # 2. Por significado: los k vectores más parecidos al de la pregunta
    vector = vector_de_pregunta(pregunta)
    por_significado = cliente.search(index=INDICE, body={
        "size": TOP, "_source": CAMPOS,
        "query": {"knn": {"vector": {"vector": vector, "k": TOP}}},
    })
    mostrar("Por significado (vector):", por_significado)

    # 3. Igual, pero solo fragmentos de contenido: el filtro va dentro de knn, así OpenSearch
    #    filtra mientras busca y siempre devuelve k resultados
    sin_referencias = cliente.search(index=INDICE, body={
        "size": TOP, "_source": CAMPOS,
        "query": {"knn": {"vector": {
            "vector": vector, "k": TOP, "filter": {"term": {"tipo": "contenido"}},
        }}},
    })
    mostrar("Por significado, sin referencias:", sin_referencias)


if __name__ == "__main__":
    main()
