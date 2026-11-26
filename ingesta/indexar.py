"""Vectoriza los fragmentos del artículo con vLLM y los guarda en el índice de OpenSearch.

Uso (desde la carpeta ingesta, con opensearch y vllm encendidos):  uv run python indexar.py

Lee fragmentos.jsonl, le pide a vLLM el vector de cada fragmento (en lotes), comprueba que cada
vector tenga 2048 números y mida 1, y guarda cada fragmento con su vector en el índice que creó
crear_indice.py. Volver a correrlo reemplaza los documentos en vez de duplicarlos, porque el _id
de cada documento es el id del fragmento.
"""

import json
import math
import os
import time

import httpx
from opensearchpy import OpenSearch, helpers

from crear_indice import DIMENSION, FRAGMENTOS, INDICE, URL_OPENSEARCH

# Endpoint de embeddings de vLLM (compatible con la API Embed v2 de Cohere).
# 127.0.0.1 y no "localhost": en Windows, Python prueba primero ::1 (IPv6), donde Docker no
# escucha, y espera todo el timeout antes de pasar a 127.0.0.1. Dentro de Docker se cambia con
# la variable de entorno (ahí el servidor se llama "vllm")
URL_EMBED = os.environ.get("URL_EMBED", "http://127.0.0.1:8000/v2/embed")
# Modelo que atiende vLLM (el mismo nombre con el que arranca en docker-compose.yml)
MODELO = "nvidia/Nemotron-3-Embed-1B-BF16"
# Cuántos fragmentos se mandan a vLLM en cada petición
TAMANO_LOTE = 16
# Margen para aceptar que un vector mide 1 (por redondeo nunca da 1 exacto)
TOLERANCIA_LARGO = 1e-3


def vectorizar(cliente_http: httpx.Client, textos: list[str]) -> list[list[float]]:
    """Le pide a vLLM los vectores de varios textos de una vez, tratados como documentos."""
    respuesta = cliente_http.post(URL_EMBED, json={
        "model": MODELO,
        # vLLM le antepone "passage: " a cada texto (las preguntas irán con "query": "query: ")
        "input_type": "document",
        "texts": textos,
        "embedding_types": ["float"],
        # Si un texto no cupiera en el modelo, que dé error en vez de cortarlo en silencio
        "truncate": "NONE",
    })
    # Si vLLM responde con error, el script se detiene aquí y muestra el motivo
    respuesta.raise_for_status()
    return respuesta.json()["embeddings"]["float"]


def revisar_vector(vector: list[float], id_fragmento: str) -> float:
    """Comprueba que el vector tenga 2048 números y mida 1; devuelve cuánto mide."""
    if len(vector) != DIMENSION:
        raise ValueError(f"{id_fragmento}: el vector tiene {len(vector)} números, no {DIMENSION}")
    # Largo del vector: raíz de la suma de sus números al cuadrado
    largo = math.sqrt(sum(x * x for x in vector))
    # El índice usa innerproduct, que solo equivale al coseno si los vectores miden 1
    if abs(largo - 1) > TOLERANCIA_LARGO:
        raise ValueError(f"{id_fragmento}: el vector mide {largo:.6f}, no 1")
    return largo


def indexar_fragmentos(cliente: OpenSearch, fragmentos: list[dict], avisar=print
                       ) -> tuple[int, list[float], float]:
    """Vectoriza los fragmentos con vLLM y los guarda en el índice. Devuelve cuántos guardó, el
    largo de cada vector y los segundos que tardó la vectorización.

    La usan este script (el survey original) y el trabajador de los artículos subidos.
    """
    # 1. Vectorizar en lotes
    inicio = time.perf_counter()
    largos = []
    # Una sola conexión para todas las peticiones; espera hasta 120 s por respuesta
    with httpx.Client(timeout=120) as cliente_http:
        for i in range(0, len(fragmentos), TAMANO_LOTE):
            lote = fragmentos[i:i + TAMANO_LOTE]
            # Se vectoriza lo mismo donde busca BM25: títulos + texto
            vectores = vectorizar(cliente_http, [f["texto_para_buscar"] for f in lote])
            for fragmento, vector in zip(lote, vectores, strict=True):
                largos.append(revisar_vector(vector, fragmento["id"]))
                fragmento["vector"] = vector
            avisar(f"  vectorizados {i + len(lote)} de {len(fragmentos)}")
    segundos = time.perf_counter() - inicio

    # 2. Guardar en OpenSearch: una acción por fragmento, con su id como _id del documento
    acciones = ({"_index": INDICE, "_id": f["id"], "_source": f} for f in fragmentos)
    # refresh=True: los documentos se pueden buscar en cuanto termina
    cargados, _ = helpers.bulk(cliente, acciones, refresh=True)
    return cargados, largos, segundos


def main() -> None:
    with FRAGMENTOS.open(encoding="utf-8") as archivo:
        fragmentos = [json.loads(linea) for linea in archivo]
    cliente = OpenSearch(hosts=[URL_OPENSEARCH])
    if not cliente.indices.exists(index=INDICE):
        raise SystemExit(f"No existe el índice {INDICE}; primero: uv run python crear_indice.py")

    cargados, largos, segundos = indexar_fragmentos(cliente, fragmentos)

    # 3. Resumen para revisar que todo salió bien
    print(f"\nVectores: {len(largos)} de {DIMENSION} números, largo entre {min(largos):.6f} "
          f"y {max(largos):.6f}, en {segundos:.1f} s")
    print(f"Guardados en {INDICE}: {cargados}")
    print(f"Documentos en el índice: {cliente.count(index=INDICE)['count']}")
    # Cuántos hay de cada tipo, contados por OpenSearch (agregación sobre el campo keyword "tipo")
    respuesta = cliente.search(index=INDICE, body={
        "size": 0, "aggs": {"por_tipo": {"terms": {"field": "tipo"}}},
    })
    for grupo in respuesta["aggregations"]["por_tipo"]["buckets"]:
        print(f"  {grupo['key']:<11} {grupo['doc_count']}")


if __name__ == "__main__":
    main()
