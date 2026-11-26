"""Convierte el PDF del artículo con Docling y guarda el resultado para revisarlo.

Uso (desde la carpeta ingesta):  uv run python convertir.py
"""

import time
from pathlib import Path

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import ImageRefMode, SectionHeaderItem

# Carpeta raíz del repo: un nivel arriba de esta carpeta "ingesta"
RAIZ = Path(__file__).resolve().parent.parent
# PDF del artículo que se va a convertir
PDF = RAIZ / "datos" / "huang2026-rag-survey.pdf"
# Carpeta donde se guarda lo que produce Docling
SALIDA = RAIZ / "datos" / "procesado"


def crear_convertidor(imagenes: bool = True, hilos: int = 8) -> DocumentConverter:
    """Arma el convertidor de Docling con las opciones que necesita este PDF.

    Con imagenes=False no guarda la imagen de cada figura: es lo que usa el trabajador de los
    artículos subidos, porque las imágenes no se usan para responder y ocupan RAM.
    """
    opciones = PdfPipelineOptions()
    # El PDF es digital: el texto se lee directo y no hace falta OCR
    opciones.do_ocr = False
    # Reconocer filas y columnas de las tablas
    opciones.do_table_structure = True
    # Modelo de tablas en modo preciso: más lento que FAST, pero mejor con tablas complejas
    opciones.table_structure_options.mode = TableFormerMode.ACCURATE
    # Guardar la imagen de cada figura, para revisarlas y para el modelo de visión más adelante
    opciones.generate_picture_images = imagenes
    # Resolución de esas imágenes: 2 = el doble del tamaño original de la página
    opciones.images_scale = 2.0 if imagenes else 1.0
    # Correr en CPU; 8 hilos = uno por núcleo físico del Ryzen 9 270
    opciones.accelerator_options = AcceleratorOptions(
        device=AcceleratorDevice.CPU, num_threads=hilos
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opciones)}
    )


def pagina(elemento) -> int | str:
    """Número de página donde aparece un elemento del documento."""
    return elemento.prov[0].page_no if elemento.prov else "?"


def main() -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    convertidor = crear_convertidor()

    print(f"Convirtiendo {PDF.name} ...")
    inicio = time.perf_counter()
    resultado = convertidor.convert(PDF)
    segundos = time.perf_counter() - inicio
    doc = resultado.document

    # JSON: el documento completo y sin pérdidas; de aquí saldrán los fragmentos
    doc.save_as_json(SALIDA / "articulo.json", image_mode=ImageRefMode.REFERENCED)
    # Markdown: versión legible para revisar a ojo cómo quedó la conversión
    doc.save_as_markdown(SALIDA / "articulo.md", image_mode=ImageRefMode.REFERENCED)

    print(f"Listo en {segundos:.0f} s. Estado: {resultado.status.value}")
    print(f"Páginas: {doc.num_pages()}")

    # Cuántos elementos de cada tipo encontró (párrafos, títulos, tablas, figuras...)
    conteo: dict[str, int] = {}
    for elemento, _profundidad in doc.iterate_items():
        tipo = elemento.label.value
        conteo[tipo] = conteo.get(tipo, 0) + 1
    print("\nElementos por tipo:")
    for tipo, cantidad in sorted(conteo.items(), key=lambda par: -par[1]):
        print(f"  {tipo:<16} {cantidad}")

    # Títulos de sección con su nivel y su página
    print("\nSecciones (página, nivel, título):")
    for elemento, _profundidad in doc.iterate_items():
        if isinstance(elemento, SectionHeaderItem):
            print(f"  p.{pagina(elemento):<3} n{elemento.level}  {elemento.text}")

    # Tablas: página, tamaño y pie
    print("\nTablas (página, filas x columnas, pie):")
    for numero, tabla in enumerate(doc.tables, start=1):
        tamano = f"{tabla.data.num_rows}x{tabla.data.num_cols}"
        print(f"  {numero}. p.{pagina(tabla):<3} {tamano:<6} {tabla.caption_text(doc)[:80]}")

    # Figuras: página y pie
    print("\nFiguras (página, pie):")
    for numero, figura in enumerate(doc.pictures, start=1):
        print(f"  {numero}. p.{pagina(figura):<3} {figura.caption_text(doc)[:80]}")

    print(f"\nArchivos guardados en {SALIDA}")


if __name__ == "__main__":
    main()
