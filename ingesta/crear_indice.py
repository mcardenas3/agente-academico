"""Crea en OpenSearch el índice donde vivirán los fragmentos del artículo: texto para BM25 y vector.

Uso (desde la carpeta ingesta, con OpenSearch encendido):
    uv run python crear_indice.py             crea el índice si no existe y muestra cómo quedó
    uv run python crear_indice.py --recrear   lo borra (con todos sus documentos) y lo crea de nuevo

Un solo índice con el texto y el vector juntos, porque la búsqueda híbrida combina las dos
búsquedas sobre los mismos documentos. Los vectores se cargan después, con otro script (paso 6).
"""

import argparse
import json
from pathlib import Path

from opensearchpy import OpenSearch

# Fragmentos que genera fragmentar.py; aquí solo se leen sus llaves para comprobar el mapping.
# La ruta se arma a mano para no importar fragmentar.py, que carga Docling y transformers
FRAGMENTOS = Path(__file__).resolve().parent.parent / "datos" / "procesado" / "fragmentos.jsonl"
# Dirección de OpenSearch (solo escucha en esta computadora)
URL_OPENSEARCH = "http://localhost:9200"
# Nombre del índice
INDICE = "fragmentos"
# Tamaño de los vectores de nvidia/Nemotron-3-Embed-1B-BF16 (comprobado con vLLM en la Etapa 2)
DIMENSION = 2048

# Configuración del índice: ajustes generales (settings) y tipo de cada campo (mappings)
CONFIGURACION = {
    "settings": {
        "index": {
            # Un solo pedazo (shard): son 268 documentos
            "number_of_shards": 1,
            # Sin copias: solo hay un servidor; con 1 copia no habría dónde ponerla y el índice
            # quedaría en amarillo
            "number_of_replicas": 0,
            # Activa la búsqueda por vectores en este índice (por default está apagada)
            "knn": True,
            # Cuántos candidatos revisa Faiss en cada búsqueda por vectores; 100 es el default.
            # Con 268 vectores, revisar 100 es casi una búsqueda exacta
            "knn.algo_param.ef_search": 100,
        }
    },
    "mappings": {
        # Rechaza documentos con campos no declarados aquí (una errata no pasa en silencio)
        "dynamic": "strict",
        "properties": {
            # keyword: el valor se guarda tal cual, sirve para filtrar y para citar
            "id": {"type": "keyword"},
            # "contenido" o "referencia"; sirve para excluir las referencias de una búsqueda
            "tipo": {"type": "keyword"},
            # Ruta de títulos, por ejemplo "2 RAG Framework > 2.2 RAG Paradigm"
            "seccion": {"type": "keyword"},
            # Los mismos títulos como lista; un campo de OpenSearch acepta varios valores
            "secciones": {"type": "keyword"},
            # Páginas del PDF de donde viene el fragmento (lista de enteros)
            "paginas": {"type": "integer"},
            # Tipos de elementos que trae: text, table, code, list_item...
            "elementos": {"type": "keyword"},
            # Número de la referencia ("[48]" → 48); null en los fragmentos de contenido
            "numero_referencia": {"type": "integer"},
            # Lo que se le da al LLM: se guarda, pero no se busca en él (index: false)
            "texto": {"type": "text", "index": False},
            # Títulos + texto: aquí busca BM25. Analizador english (deja las palabras en su
            # raíz), elegido con probar_analizadores.py
            "texto_para_buscar": {"type": "text", "analyzer": "english"},
            # Tamaño del fragmento en tokens del modelo de embeddings
            "tokens": {"type": "integer"},
            # Título y DOI del artículo, para citar
            "articulo": {"type": "keyword"},
            "doi": {"type": "keyword"},
            # El vector del fragmento (se llena en el paso 6)
            "vector": {
                "type": "knn_vector",
                "dimension": DIMENSION,
                # Producto punto: los vectores vienen normalizados (largo 1), así que es igual al
                # coseno; la documentación recomienda innerproduct en ese caso
                "space_type": "innerproduct",
                "method": {
                    # HNSW: grafo donde cada vector se une con sus vecinos cercanos
                    "name": "hnsw",
                    # Faiss: motor por default de OpenSearch 3; permite filtrar durante la búsqueda
                    "engine": "faiss",
                    # Valores por default: conexiones por vector (m) y cuidado al armar el grafo
                    # (ef_construction). Con 268 vectores no vale la pena ajustarlos
                    "parameters": {"m": 16, "ef_construction": 100},
                },
            },
        },
    },
}


def revisar_indice(cliente: OpenSearch) -> None:
    """Lee de OpenSearch cómo quedó el índice y lo compara con las llaves de los fragmentos."""
    # flat_settings da los ajustes como "index.knn" en vez de anidados
    ajustes = cliente.indices.get_settings(index=INDICE, flat_settings=True)[INDICE]["settings"]
    mapping = cliente.indices.get_mapping(index=INDICE)[INDICE]["mappings"]
    salud = cliente.cluster.health(index=INDICE)["status"]

    print(f"\nÍndice: {INDICE}   estado: {salud}")
    for ajuste in ["number_of_shards", "number_of_replicas", "knn", "knn.algo_param.ef_search"]:
        print(f"  index.{ajuste} = {ajustes.get('index.' + ajuste)}")
    print(f"  dynamic = {mapping.get('dynamic')}")

    print("\nCampos:")
    campos = mapping["properties"]
    for nombre, definicion in campos.items():
        if definicion["type"] == "knn_vector":
            # El vector se muestra completo: dimensión, métrica y método
            detalle = json.dumps({k: v for k, v in definicion.items() if k != "type"})
        else:
            # De los demás, solo lo que no es default: analizador o index: false
            detalle = ", ".join(f"{k}: {v}" for k, v in definicion.items() if k != "type")
        print(f"  {nombre:<18} {definicion['type']:<11} {detalle}")

    # Un campo por cada llave de fragmentos.jsonl, más el vector
    with FRAGMENTOS.open(encoding="utf-8") as archivo:
        llaves = set(json.loads(archivo.readline())) | {"vector"}
    faltan = sorted(llaves - set(campos))
    sobran = sorted(set(campos) - llaves)
    print(f"\nLlaves de fragmentos.jsonl + vector: {len(llaves)}   campos en el índice: "
          f"{len(campos)}   faltan: {faltan or 'ninguno'}   sobran: {sobran or 'ninguno'}")
    print(f"Documentos en el índice: {cliente.count(index=INDICE)['count']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea el índice de fragmentos en OpenSearch.")
    parser.add_argument("--recrear", action="store_true",
                        help="borra el índice si ya existe (con todos sus documentos) y lo crea")
    argumentos = parser.parse_args()
    cliente = OpenSearch(hosts=[URL_OPENSEARCH])

    existe = cliente.indices.exists(index=INDICE)
    if existe and argumentos.recrear:
        cliente.indices.delete(index=INDICE)
        print(f"Índice {INDICE} borrado")
        existe = False
    if existe:
        # Casi nada del mapping se puede cambiar después: para cambiarlo hay que recrearlo
        print(f"El índice {INDICE} ya existe; no se cambió nada. "
              "Para borrarlo y crearlo de nuevo: uv run python crear_indice.py --recrear")
    else:
        cliente.indices.create(index=INDICE, body=CONFIGURACION)
        print(f"Índice {INDICE} creado")

    revisar_indice(cliente)


if __name__ == "__main__":
    main()
