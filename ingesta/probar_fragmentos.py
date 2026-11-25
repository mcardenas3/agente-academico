"""Experimento: compara tamaños de fragmento y formatos de tabla antes de elegir uno.

Uso (desde la carpeta ingesta):  uv run python probar_fragmentos.py

Parte el artículo ya limpio (salida de limpiar.py) con el HybridChunker de Docling y, para cada
combinación de tamaño máximo y formato de tabla, mide cuántos fragmentos salen y de qué tamaño.
"""

import statistics
import time

from docling_core.transforms.chunker.hierarchical_chunker import (
    ChunkingSerializerProvider,
)
from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from docling_core.types.doc import DoclingDocument
from transformers import AutoTokenizer
from transformers.utils import logging as registro_transformers

# El modelo y el formato Markdown se definen en fragmentar.py, que es el que se usa de verdad
from fragmentar import MODELO_EMBEDDINGS, ProveedorMarkdown
from limpiar import SALIDA as ARTICULO_LIMPIO

# Tamaños máximos de fragmento que se comparan, en tokens
TAMANOS = [256, 512, 1024]
# Página donde está la Tabla 3, la tabla grande del artículo
PAGINA_TABLA_3 = 23


# Formatos de tabla que se comparan
FORMATOS = {
    # El de Docling: una frase por celda, "fila, columna = valor"
    "tripletas": ChunkingSerializerProvider(),
    # Una línea por fila: "| REALM [48] | 2020 | ✓ | ... |"
    "markdown": ProveedorMarkdown(),
}


def medir(doc: DoclingDocument, chunker: HybridChunker, tabla_3_ref: str) -> dict:
    """Parte el documento y resume cuántos fragmentos salieron y de qué tamaño."""
    inicio = time.perf_counter()
    fragmentos = list(chunker.chunk(doc))
    segundos = time.perf_counter() - inicio

    # contextualize() = títulos de sección + texto: es lo que se va a vectorizar
    tamanos = [chunker.tokenizer.count_tokens(chunker.contextualize(f)) for f in fragmentos]

    def contiene(fragmento, condicion) -> bool:
        """Dice si algún elemento del fragmento cumple la condición."""
        return any(condicion(elemento) for elemento in fragmento.meta.doc_items)

    return {
        "fragmentos": len(fragmentos),
        # El último título del fragmento dice a qué sección pertenece
        "referencias": sum(f.meta.headings[-1] == "References" for f in fragmentos),
        "con tabla": sum(contiene(f, lambda e: e.label.value == "table") for f in fragmentos),
        "tabla 3": sum(contiene(f, lambda e: e.self_ref == tabla_3_ref) for f in fragmentos),
        "mediana": round(statistics.median(tamanos)),
        "máximo": max(tamanos),
        "pasan": sum(t > chunker.tokenizer.get_max_tokens() for t in tamanos),
        "total": sum(tamanos),
        "segundos": round(segundos, 1),
    }


def main() -> None:
    # Oculta avisos de transformers sobre la configuración del modelo que no afectan al tokenizador
    registro_transformers.set_verbosity_error()
    doc = DoclingDocument.load_from_json(ARTICULO_LIMPIO)
    # La primera vez baja solo los archivos del tokenizador (~17 MB), no los pesos del modelo
    base = AutoTokenizer.from_pretrained(MODELO_EMBEDDINGS)
    tabla_3 = next(t for t in doc.tables if t.prov[0].page_no == PAGINA_TABLA_3)

    # Cuántos tokens ocupa la Tabla 3 sola en cada formato
    print("Tabla 3 completa:")
    for formato, proveedor in FORMATOS.items():
        texto = proveedor.get_serializer(doc).serialize(item=tabla_3).text
        print(f"  {formato:<10} {len(base.tokenize(texto))} tokens")

    columnas = ["fragmentos", "referencias", "con tabla", "tabla 3", "mediana", "máximo", "pasan",
                "total", "segundos"]
    print("\n| formato | máx. tokens | " + " | ".join(columnas) + " |")
    print("|---" * (len(columnas) + 2) + "|")
    for formato, proveedor in FORMATOS.items():
        for tamano in TAMANOS:
            tokenizador = HuggingFaceTokenizer(tokenizer=base, max_tokens=tamano)
            chunker = HybridChunker(tokenizer=tokenizador, serializer_provider=proveedor)
            medidas = medir(doc, chunker, tabla_3.self_ref)
            print(f"| {formato} | {tamano} | " + " | ".join(str(medidas[c]) for c in columnas) + " |")

    # Muestra: cómo se ve un fragmento ya contextualizado (512 tokens, tablas en Markdown)
    tokenizador = HuggingFaceTokenizer(tokenizer=base, max_tokens=512)
    chunker = HybridChunker(tokenizer=tokenizador, serializer_provider=FORMATOS["markdown"])
    fragmentos = list(chunker.chunk(doc))
    for buscado in ["2.2.3 Post-Retrieval", None]:
        if buscado:
            # Un fragmento de texto normal: la subsección que se separó en limpiar.py
            muestra = next(f for f in fragmentos if f.meta.headings[-1] == buscado)
        else:
            # Un fragmento de la Tabla 3, para ver que repite el encabezado de la tabla
            muestra = [f for f in fragmentos
                       if any(e.self_ref == tabla_3.self_ref for e in f.meta.doc_items)][1]
        paginas = sorted({p.page_no for e in muestra.meta.doc_items for p in e.prov})
        print(f"\n--- Muestra (títulos: {' > '.join(muestra.meta.headings)}; páginas: {paginas})")
        print(chunker.contextualize(muestra)[:700])


if __name__ == "__main__":
    main()
