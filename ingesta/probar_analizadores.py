"""Experimento: ¿con qué analizador encuentra mejor BM25 los fragmentos, standard o english?

Uso (desde la carpeta ingesta, con OpenSearch encendido):  uv run python probar_analizadores.py

Crea un índice temporal por configuración (solo con el texto, sin vectores), carga los 268
fragmentos, hace 15 preguntas en inglés de las que ya se sabe qué fragmento las responde, mide en
qué lugar sale ese fragmento y al final borra los índices temporales.
"""

import json
from pathlib import Path

from opensearchpy import OpenSearch, helpers

# Fragmentos que genera fragmentar.py. La ruta se arma aquí en vez de importarla de fragmentar.py
# porque importarlo cargaría Docling y transformers, que tardan y gastan RAM
FRAGMENTOS = Path(__file__).resolve().parent.parent / "datos" / "procesado" / "fragmentos.jsonl"
# Dirección de OpenSearch (solo escucha en esta computadora).
# 127.0.0.1 y no "localhost": en Windows, Python prueba primero ::1 (IPv6), donde Docker no
# escucha, y espera todo el timeout antes de pasar a 127.0.0.1
URL_OPENSEARCH = "http://127.0.0.1:9200"
# Cuántos resultados se revisan por pregunta
TOP = 10
# Inicio del id de cada fragmento; le sigue su número con tres dígitos
PREFIJO_ID = "huang2026-rag-survey-"

# Configuraciones a comparar: nombre → cómo se declara el campo de texto en el índice
CONFIGURACIONES = {
    # Solo parte en palabras y pasa a minúsculas
    "standard": {"type": "text", "analyzer": "standard"},
    # Además quita palabras vacías ("the", "in") y deja cada palabra en su raíz ("retriev")
    "english": {"type": "text", "analyzer": "english"},
    # Las dos a la vez: english en el campo y standard en un subcampo llamado "exacto";
    # la búsqueda suma los puntos de los dos
    "english+standard": {
        "type": "text",
        "analyzer": "english",
        "fields": {"exacto": {"type": "text", "analyzer": "standard"}},
    },
}

# Preguntas de prueba, escritas como las reescribiría el LLM al inglés a partir de una pregunta en
# español. Junto a cada una, los números de los fragmentos que la responden (se revisaron a mano
# leyendo fragmentos.jsonl ANTES de correr el experimento, para no acomodarlas al resultado)
PREGUNTAS = [
    # ¿Por qué alucinan los modelos de lenguaje y cómo ayuda RAG?
    ("Why do large language models hallucinate and how does RAG help?", [5]),
    # ¿Cuáles son las fases del paradigma RAG?
    ("What are the phases of the RAG paradigm?", [10]),
    # ¿En qué se diferencian BM25 y los recuperadores basados en modelos de lenguaje?
    ("How do BM25 and retrievers based on pretrained language models differ?", [8]),
    # ¿Cómo funciona el hashing sensible a la localidad para buscar vecinos cercanos?
    ("How does locality-sensitive hashing find nearest neighbors?", [17]),
    # ¿Qué estrategias existen para modificar la consulta del usuario?
    ("What strategies exist for modifying the user's query?", [12, 20]),
    # ¿Qué es la reformulación de consultas y qué métodos la usan?
    ("What is query reformulation and which methods use it?", [21]),
    # ¿Qué diferencia hay entre la recuperación iterativa y la recursiva?
    ("What is the difference between iterative and recursive retrieval?", [25, 26, 27]),
    # En la recuperación adaptativa, ¿cómo decide el modelo cuándo recuperar más información?
    ("In adaptive retrieval, how does the model decide when to retrieve more information?",
     [28, 32]),
    # ¿Cómo funcionan los modelos supervisados de reordenamiento?
    ("How do supervised re-ranking models work?", [34]),
    # ¿Para qué sirve filtrar los documentos recuperados?
    ("Why filter the retrieved documents after retrieval?", [36, 37]),
    # ¿Cómo se combinan varios documentos recuperados al generar la respuesta?
    ("How are several retrieved documents combined when generating the answer?", [39]),
    # ¿Cómo se personalizan las respuestas para cada usuario?
    ("How are responses personalized for each user?", [41, 42]),
    # ¿Qué métricas y conjuntos de datos se usan para evaluar RAG?
    ("Which metrics and datasets are used to evaluate RAG?", [43]),
    # ¿Cómo se comprime el contexto para que la generación sea más rápida?
    ("How can the context be compressed to speed up generation?", [69]),
    # ¿Más documentos en un contexto largo siempre mejoran la respuesta?
    ("Do more documents in a long context always improve the answer?", [71]),
]


def crear_indice_temporal(cliente: OpenSearch, nombre: str, campo_texto: dict) -> None:
    """Crea (o recrea) un índice con un solo campo: el texto donde busca BM25."""
    if cliente.indices.exists(index=nombre):
        cliente.indices.delete(index=nombre)
    cliente.indices.create(
        index=nombre,
        body={
            # Un solo pedazo y sin copias: solo hay un servidor
            "settings": {"number_of_shards": 1, "number_of_replicas": 0},
            "mappings": {"properties": {"texto_para_buscar": campo_texto}},
        },
    )


def cargar_fragmentos(cliente: OpenSearch, nombre: str, fragmentos: list[dict]) -> int:
    """Mete todos los fragmentos en el índice de una sola vez y devuelve cuántos entraron."""
    # Una "acción" por fragmento: a qué índice va, con qué id y qué campos lleva
    acciones = (
        {"_index": nombre, "_id": f["id"], "texto_para_buscar": f["texto_para_buscar"]}
        for f in fragmentos
    )
    # bulk los manda en paquetes; refresh=True hace que se puedan buscar en cuanto termina
    cargados, _ = helpers.bulk(cliente, acciones, refresh=True)
    return cargados


def lugar_del_relevante(cliente: OpenSearch, nombre: str, campos: list[str], pregunta: str,
                        relevantes: list[int]) -> int | None:
    """Busca la pregunta y devuelve el lugar del primer fragmento que la responde (o None)."""
    respuesta = cliente.search(
        index=nombre,
        body={
            "size": TOP,
            # Solo interesan los ids; no hace falta que devuelva el texto
            "_source": False,
            # multi_match con type most_fields suma los puntos de BM25 de cada campo
            "query": {"multi_match": {"query": pregunta, "fields": campos,
                                      "type": "most_fields"}},
        },
    )
    ids_relevantes = {f"{PREFIJO_ID}{n:03d}" for n in relevantes}
    for lugar, resultado in enumerate(respuesta["hits"]["hits"], start=1):
        if resultado["_id"] in ids_relevantes:
            return lugar
    return None


def main() -> None:
    with FRAGMENTOS.open(encoding="utf-8") as archivo:
        fragmentos = [json.loads(linea) for linea in archivo]
    cliente = OpenSearch(hosts=[URL_OPENSEARCH])

    # lugares[configuración] = lugar del fragmento correcto en cada pregunta
    lugares = {}
    try:
        for config, campo_texto in CONFIGURACIONES.items():
            # Los nombres de índice no admiten "+"
            nombre = "prueba-analizador-" + config.replace("+", "-")
            crear_indice_temporal(cliente, nombre, campo_texto)
            cargados = cargar_fragmentos(cliente, nombre, fragmentos)
            print(f"Índice {nombre}: {cargados} fragmentos cargados")
            campos = ["texto_para_buscar"]
            if "fields" in campo_texto:
                campos.append("texto_para_buscar.exacto")
            lugares[config] = [
                lugar_del_relevante(cliente, nombre, campos, pregunta, relevantes)
                for pregunta, relevantes in PREGUNTAS
            ]
    finally:
        # Pase lo que pase, se borran los índices temporales
        for config in CONFIGURACIONES:
            cliente.indices.delete(index="prueba-analizador-" + config.replace("+", "-"),
                                   ignore_unavailable=True)

    # Tabla por pregunta: lugar en que salió el fragmento correcto ("-" = no salió en el top 10)
    nombres = list(CONFIGURACIONES)
    print(f"\n{'#':>2}  {'Pregunta':<62}" + "".join(f"{n:>18}" for n in nombres))
    for i, (pregunta, _) in enumerate(PREGUNTAS):
        celdas = "".join(f"{lugares[n][i] or '-':>18}" for n in nombres)
        print(f"{i + 1:>2}  {pregunta[:60]:<62}{celdas}")

    # Resumen: cuántas preguntas encontraron su fragmento en el lugar 1 y en los 5 primeros, y el
    # MRR (promedio de 1/lugar: vale 1 si siempre sale primero, 0.5 si siempre sale segundo...)
    print(f"\n{'Configuración':<18}{'Top 1':>7}{'Top 5':>7}{'Top 10':>8}{'MRR':>7}")
    for n in nombres:
        encontrados = [lugar for lugar in lugares[n] if lugar]
        top1 = sum(lugar == 1 for lugar in encontrados)
        top5 = sum(lugar <= 5 for lugar in encontrados)
        mrr = sum(1 / lugar for lugar in encontrados) / len(PREGUNTAS)
        print(f"{n:<18}{top1:>7}{top5:>7}{len(encontrados):>8}{mrr:>7.3f}")


if __name__ == "__main__":
    main()
