"""Parte el artículo ya limpio en fragmentos que saben de qué sección y de qué páginas vienen.

Uso (desde la carpeta ingesta):  uv run python fragmentar.py

Lee articulo_limpio.json (salida de limpiar.py) y guarda un fragmento por línea en
datos/procesado/fragmentos.jsonl, listo para vectorizarse e indexarse en OpenSearch.
Las decisiones (512 tokens, tablas en Markdown, una referencia por fragmento, sin el título del
artículo en el texto) salieron del experimento de probar_fragmentos.py.
"""

import json
import re
import statistics

from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingDocSerializer,
    ChunkingSerializerProvider,
)
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from docling_core.transforms.serializer.markdown import MarkdownTableSerializer
from docling_core.types.doc import (
    DoclingDocument,
    ListItem,
    SectionHeaderItem,
    TitleItem,
)
from transformers import AutoTokenizer
from transformers.utils import logging as registro_transformers

from limpiar import PROCESADO
from limpiar import SALIDA as ARTICULO_LIMPIO

# Archivo de salida: un fragmento por línea, en formato JSON
SALIDA = PROCESADO / "fragmentos.jsonl"
# Modelo de embeddings: se usa su tokenizador para medir los fragmentos igual que él los mide
MODELO_EMBEDDINGS = "nvidia/Nemotron-3-Embed-1B-BF16"
# Tamaño máximo de cada fragmento, en tokens (elegido con probar_fragmentos.py)
MAX_TOKENS = 512
# Identificador del artículo (DOI); se guarda en cada fragmento para poder citarlo
DOI = "10.1145/3805774"
# Títulos con que suele empezar la bibliografía (con o sin número delante: "7 References")
TITULOS_DE_REFERENCIAS = {"references", "bibliography", "referencias", "bibliografía",
                          "works cited", "literature cited"}
# Nombre para lo que va antes de la primera sección (autores, resumen, datos de publicación),
# que en el PDF no tiene título
SECCION_INICIAL = "Front matter"


class ProveedorMarkdown(ChunkingSerializerProvider):
    """Igual que el de Docling, pero escribe las tablas en Markdown en vez de tripletas."""

    def get_serializer(self, doc: DoclingDocument) -> ChunkingDocSerializer:
        return ChunkingDocSerializer(doc=doc, table_serializer=MarkdownTableSerializer())


def es_referencias(titulo: str) -> bool:
    """Dice si un título es el de la bibliografía."""
    return re.sub(r"^\d+(\.\d+)*\s+", "", titulo.strip()).lower() in TITULOS_DE_REFERENCIAS


def crear_fragmento(texto: str, secciones: list[str], elementos: list, contexto: dict) -> dict:
    """Arma un fragmento con su texto y sus metadatos (sección, páginas, tipo de elementos)."""
    # Los títulos van antes del texto: así el vector y la búsqueda por palabras saben de qué
    # sección habla el fragmento
    encabezados = secciones or [SECCION_INICIAL]
    texto_para_buscar = "\n".join(encabezados + [texto])
    return {
        "seccion": " > ".join(encabezados),
        "secciones": encabezados,
        # Páginas de todos los elementos que forman el fragmento, sin repetir
        "paginas": sorted({p.page_no for e in elementos for p in e.prov}),
        # Qué tipo de elementos trae: text, table, caption, list_item, code...
        "elementos": sorted({e.label.value for e in elementos}),
        # Lo que se le muestra al LLM
        "texto": texto,
        # Lo que se vectoriza y donde busca BM25: títulos + texto
        "texto_para_buscar": texto_para_buscar,
        "tokens": contexto["tokenizador"].count_tokens(texto_para_buscar),
        "articulo": contexto["titulo"],
        # Con qué artículo de la biblioteca se filtra la búsqueda
        "articulo_id": contexto["articulo_id"],
        "doi": contexto["doi"],
    }


def fragmentos_de_contenido(doc: DoclingDocument, contexto: dict) -> list[dict]:
    """Parte el cuerpo del artículo con el HybridChunker, sin las referencias."""
    chunker = HybridChunker(tokenizer=contexto["tokenizador"],
                            serializer_provider=ProveedorMarkdown())
    fragmentos = []
    for pedazo in chunker.chunk(doc):
        secciones = pedazo.meta.headings or []
        if secciones and es_referencias(secciones[-1]):
            # Las referencias se arman aparte, una por fragmento
            continue
        fragmento = crear_fragmento(pedazo.text, secciones, pedazo.meta.doc_items, contexto)
        fragmento["tipo"] = "contenido"
        fragmento["numero_referencia"] = None
        fragmentos.append(fragmento)
    return fragmentos


def fragmentos_de_referencias(doc: DoclingDocument, contexto: dict) -> list[dict]:
    """Un fragmento por cada referencia de la bibliografía, con su número."""
    fragmentos = []
    titulo_referencias = None  # el título tal como viene en el artículo, si se está dentro
    for elemento, _ in doc.iterate_items():
        if isinstance(elemento, SectionHeaderItem):
            # Se entra a la bibliografía al ver su título y se sale al ver cualquier otro
            titulo_referencias = elemento.text if es_referencias(elemento.text) else None
        elif titulo_referencias and isinstance(elemento, ListItem):
            # El número va en el marcador de la lista: "[48]" o "48."; si no trae, se cuenta
            numero = re.search(r"\d+", elemento.marker or "")
            texto = f"{elemento.marker} {elemento.text}".strip()
            fragmento = crear_fragmento(texto, [titulo_referencias], [elemento], contexto)
            fragmento["tipo"] = "referencia"
            fragmento["numero_referencia"] = int(numero.group()) if numero else len(fragmentos) + 1
            fragmentos.append(fragmento)
    return fragmentos


def crear_tokenizador() -> HuggingFaceTokenizer:
    """El tokenizador del modelo de embeddings, para medir los fragmentos como él los mide."""
    # Oculta avisos de transformers sobre la configuración del modelo que no afectan al tokenizador
    registro_transformers.set_verbosity_error()
    base = AutoTokenizer.from_pretrained(MODELO_EMBEDDINGS)
    return HuggingFaceTokenizer(tokenizer=base, max_tokens=MAX_TOKENS)


def fragmentar_documento(doc: DoclingDocument, articulo_id: str, titulo: str, doi: str | None,
                         tokenizador: HuggingFaceTokenizer) -> list[dict]:
    """Parte un documento ya limpio en fragmentos de contenido y de bibliografía.

    La usan este script (el survey original) y el trabajador que procesa los artículos subidos.
    """
    # El chunker pondría el título del artículo como primer encabezado de todos los fragmentos
    # (y le restaría ~15 tokens al espacio de cada uno). No distingue a un fragmento de otro del
    # mismo artículo, así que se quita del documento en memoria; se guarda en "articulo"
    titulos = [e for e, _ in doc.iterate_items() if isinstance(e, TitleItem)]
    if titulos:
        doc.delete_items(node_items=titulos)
    contexto = {"tokenizador": tokenizador, "titulo": titulo, "articulo_id": articulo_id,
                "doi": doi}
    fragmentos = fragmentos_de_contenido(doc, contexto) + fragmentos_de_referencias(doc, contexto)
    # Identificador fijo para cada fragmento: id del artículo + número consecutivo
    for numero, fragmento in enumerate(fragmentos, start=1):
        fragmento["id"] = f"{articulo_id}-{numero:03d}"
    return fragmentos


def main() -> None:
    doc = DoclingDocument.load_from_json(ARTICULO_LIMPIO)
    titulo = next(e for e, _ in doc.iterate_items() if isinstance(e, TitleItem)).text
    # doc.name es el nombre del PDF sin extensión: "huang2026-rag-survey"
    fragmentos = fragmentar_documento(doc, doc.name, titulo, DOI, crear_tokenizador())

    # JSONL: un objeto JSON por línea; ensure_ascii=False deja legibles las ✓ y los acentos
    with SALIDA.open("w", encoding="utf-8") as archivo:
        for fragmento in fragmentos:
            archivo.write(json.dumps(fragmento, ensure_ascii=False) + "\n")

    # Resumen para revisar que todo salió bien
    print(f"Fragmentos: {len(fragmentos)}")
    for tipo in ["contenido", "referencia"]:
        tamanos = [f["tokens"] for f in fragmentos if f["tipo"] == tipo]
        print(f"  {tipo:<11} {len(tamanos):>4}  tokens: mínimo {min(tamanos)}, "
              f"mediana {statistics.median(tamanos):.0f}, máximo {max(tamanos)}")
    print(f"Pasan de {MAX_TOKENS} tokens: {sum(f['tokens'] > MAX_TOKENS for f in fragmentos)}")
    print(f"Sin páginas: {sum(not f['paginas'] for f in fragmentos)}")
    print(f"Secciones distintas: {len({f['seccion'] for f in fragmentos})}")
    print(f"\nGuardado en {SALIDA}")


if __name__ == "__main__":
    main()
