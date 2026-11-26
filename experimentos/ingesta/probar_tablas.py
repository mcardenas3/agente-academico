"""Experimento: compara configuraciones del modelo de tablas de Docling sobre la Tabla 3 (página 23).

Uso (desde la carpeta ingesta):  uv run python ../experimentos/ingesta/probar_tablas.py

Ronda 1 (2026-11-24): TableFormer v2 descartado; con esta tabla llega a su límite de 512 pasos
y deja todo el texto en una sola celda.
"""

import re
import sys
import time
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    TableFormerMode,
    TableStructureOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption

# Los scripts de la ingesta viven en ingesta/: se agrega esa carpeta a donde Python busca módulos
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ingesta"))
from convertir import PDF  # noqa: E402

# Página donde está la Tabla 3; convertir solo esa hace el experimento rápido
PAGINA = 23

# Las configuraciones que se comparan; solo cambia "do_cell_matching"
CONFIGURACIONES = {
    # Actual: acomoda el texto original del PDF dentro de la estructura que predice el modelo
    "v1 con cell matching (la actual)": TableStructureOptions(
        mode=TableFormerMode.ACCURATE, do_cell_matching=True
    ),
    # Alternativa: usa directamente las celdas que predice el modelo
    "v1 sin cell matching": TableStructureOptions(
        mode=TableFormerMode.ACCURATE, do_cell_matching=False
    ),
}


def medir(filas: list[list]) -> dict[str, int]:
    """Cuenta qué tan bien quedaron las filas, mirando solo la columna de estudio y la de año."""
    medidas = {"estudios": 0, "pegadas": 0, "desalineadas": 0}
    for fila in filas:
        estudio, anio = str(fila[0]).strip(), str(fila[1]).strip()
        # Cada estudio trae su número de referencia entre corchetes, por ejemplo "RAG [91]"
        citas = len(re.findall(r"\[\d+\]", estudio))
        anios = len(re.findall(r"\b20\d\d\b", anio))
        medidas["estudios"] += citas
        if citas > 1 or anios > 1:
            # Dos estudios (o dos años) metidos en una sola fila
            medidas["pegadas"] += 1
        elif bool(estudio) != bool(anio):
            # Hay estudio sin año o año sin estudio: el nombre y su fila se separaron
            medidas["desalineadas"] += 1
    return medidas


def probar(nombre: str, opciones_tabla) -> None:
    opciones = PdfPipelineOptions()
    # Igual que en convertir.py: PDF digital, sin OCR
    opciones.do_ocr = False
    # Lo único que cambia entre las pruebas: las opciones del modelo de tablas
    opciones.table_structure_options = opciones_tabla
    convertidor = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opciones)}
    )

    inicio = time.perf_counter()
    doc = convertidor.convert(PDF, page_range=(PAGINA, PAGINA)).document
    segundos = time.perf_counter() - inicio

    # La tabla más grande de la página es la Tabla 3
    tabla = max(doc.tables, key=lambda t: t.data.num_rows * t.data.num_cols)
    print(f"\n=== {nombre}  ({segundos:.0f} s)")
    print(f"Tamaño: {tabla.data.num_rows} filas x {tabla.data.num_cols} columnas")
    if tabla.data.num_cols < 2:
        print("La tabla quedó en una sola columna: no se puede medir")
        return

    filas = tabla.export_to_dataframe(doc).values.tolist()
    medidas = medir(filas)
    print(f"Estudios encontrados (por su [número]): {medidas['estudios']}")
    print(f"Filas con dos estudios pegados:         {medidas['pegadas']}")
    print(f"Filas desalineadas (estudio sin año):   {medidas['desalineadas']}")
    print("Primeras 6 filas (estudio | año):")
    for fila in filas[:6]:
        print(f"  {str(fila[0])[:40]:<40} | {fila[1]}")


def main() -> None:
    for nombre, opciones_tabla in CONFIGURACIONES.items():
        probar(nombre, opciones_tabla)


if __name__ == "__main__":
    main()
