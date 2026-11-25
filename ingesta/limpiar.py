"""Corrige el documento que produjo Docling antes de partirlo en fragmentos.

Arregla lo que se encontró al revisar la conversión: palomitas ilegibles, letras matemáticas,
títulos falsos, títulos sin jerarquía, subsecciones escritas dentro de un párrafo, algoritmos
partidos y párrafos cortados por una tabla, un algoritmo o un cambio de página.

Uso (desde la carpeta ingesta):  uv run python limpiar.py
"""

import re
import unicodedata
from pathlib import Path

from docling_core.types.doc import (
    ContentLayer,
    DocItemLabel,
    DoclingDocument,
    ImageRefMode,
    ListItem,
    SectionHeaderItem,
    TableItem,
    TextItem,
    TitleItem,
)

# Carpeta raíz del repo: un nivel arriba de esta carpeta "ingesta"
RAIZ = Path(__file__).resolve().parent.parent
# Carpeta con lo que produjo Docling
PROCESADO = RAIZ / "datos" / "procesado"
# Documento tal como salió de convertir.py
ENTRADA = PROCESADO / "articulo.json"
# Documento ya corregido: de aquí saldrán los fragmentos
SALIDA = PROCESADO / "articulo_limpio.json"

# Carácter que pone Docling cuando no puede leer una letra (la fuente no traía su equivalencia)
ILEGIBLE = chr(0xFFFD)
# En este artículo todos los caracteres ilegibles son palomitas
PALOMITA = "✓"
# Título del artículo: Docling lo marcó como una sección más
TITULO_ARTICULO = "A Survey on Retrieval-Augmented Text Generation for Large Language Models"
# Número de página leído como título, por ejemplo "300:23" (artículo 300, página 23)
NUMERO_DE_PAGINA = re.compile(r"^\d+:\d+$")
# Numeración al inicio de un título: "2", "2.1" o "2.2.2", seguida de un espacio
NUMERACION = re.compile(r"^(\d+(?:\.\d+)*)\s")
# Subsección escrita dentro del párrafo: "2.2.1 Pre-Retrieval. The pre-retrieval phase..."
# Grupo 1 = el título ("2.2.1 Pre-Retrieval"); grupo 2 = el resto del párrafo
SUBSECCION_EN_LINEA = re.compile(r"^(\d+\.\d+\.\d+ [^.]+)\.\s+(.+)$", re.DOTALL)
# Marcador de una subsección que Docling leyó como elemento de una lista numerada: "2.1.1"
MARCADOR_SUBSECCION = re.compile(r"^\d+\.\d+\.\d+$")
# Nombre de la subsección y resto del texto: "Indexing. Efficient retrieval begins..."
NOMBRE_Y_RESTO = re.compile(r"^([^.]+)\.\s+(.+)$", re.DOTALL)
# Inicio del pie de un algoritmo: "ALGORITHM 1: Iterative Retrieval Strategy in RAG"
INICIO_ALGORITMO = "ALGORITHM "
# Sección que explica los algoritmos: se juntan todos al final de ella
SECCION_ALGORITMOS = "4.3 Retrieval Strategy"
# Lugar donde empieza cada paso de un algoritmo ("7: Retrieval Phase") o la línea "Ensure:";
# ahí se pone un salto de línea, porque el PDF los dejó todos en un solo renglón
PASO_ALGORITMO = re.compile(r"\s+(?=\d+:\s|Ensure:)")
# Página de la Tabla 3
PAGINA_TABLA_3 = 23
# Inicio de la nota de la Tabla 3, que Docling metió a la mitad de un párrafo de la p.22
INICIO_NOTA_TABLA_3 = "A ✓in the"
# Un párrafo completo termina con alguno de estos signos
FIN_DE_ORACION = (".", "!", "?", ":")
# Elementos que el PDF puede meter a la mitad de un párrafo: tablas, figuras, pies, notas y código
FLOTANTES = {DocItemLabel.TABLE, DocItemLabel.PICTURE, DocItemLabel.CAPTION,
             DocItemLabel.FOOTNOTE, DocItemLabel.CODE}


def limpiar_texto(texto: str) -> str:
    """Cambia los caracteres ilegibles por palomitas y normaliza las letras con NFKC."""
    # NFKC convierte variantes "decorativas" en la letra normal: "𝑞" (q matemática) -> "q"
    texto = unicodedata.normalize("NFKC", texto)
    return texto.replace(ILEGIBLE, PALOMITA)


def limpiar_textos(doc: DoclingDocument) -> int:
    """Limpia el texto de todos los elementos y de todas las celdas de las tablas."""
    cambiados = 0
    for elemento in doc.texts:
        limpio = limpiar_texto(elemento.text)
        if limpio != elemento.text:
            elemento.text = limpio
            cambiados += 1
    for tabla in doc.tables:
        # La tabla guarda sus celdas en table_cells; la cuadrícula (grid) se arma a partir de ellas
        for celda in tabla.data.table_cells:
            limpio = limpiar_texto(celda.text)
            if limpio != celda.text:
                celda.text = limpio
                cambiados += 1
    return cambiados


def cambiar_por_texto(doc: DoclingDocument, titulo: SectionHeaderItem, etiqueta: DocItemLabel,
                      capa: ContentLayer = ContentLayer.BODY) -> None:
    """Pone un elemento de texto en el lugar de un título falso y borra el título."""
    doc.insert_text(sibling=titulo, label=etiqueta, text=titulo.text, orig=titulo.orig,
                    prov=titulo.prov[0], content_layer=capa)
    doc.delete_items(node_items=[titulo])


def corregir_titulos_falsos(doc: DoclingDocument) -> list[str]:
    """Quita la marca de sección a lo que no es una sección del artículo."""
    cambios = []
    # Se copia la lista antes de modificar el documento, para no recorrerlo mientras cambia
    titulos = [e for e, _ in doc.iterate_items() if isinstance(e, SectionHeaderItem)]
    for titulo in titulos:
        texto = titulo.text
        if texto == TITULO_ARTICULO:
            # El título del artículo pasa a ser el título del documento, no una sección
            doc.insert_title(sibling=titulo, text=texto, orig=titulo.orig, prov=titulo.prov[0])
            doc.delete_items(node_items=[titulo])
            cambios.append(f"título del documento: {texto[:50]}")
        elif NUMERO_DE_PAGINA.match(texto):
            # Encabezado de página: se manda a "furniture", igual que los otros 37 números de página
            cambiar_por_texto(doc, titulo, DocItemLabel.PAGE_HEADER, ContentLayer.FURNITURE)
            cambios.append(f"encabezado de página: {texto}")
        elif texto.startswith("ALGORITHM "):
            # Nombre de un algoritmo: es un pie, como el de una figura
            cambiar_por_texto(doc, titulo, DocItemLabel.CAPTION)
            cambios.append(f"pie: {texto}")
        elif texto == "ACM Reference Format:":
            # Inicio de la forma de citar el artículo: es texto normal
            cambiar_por_texto(doc, titulo, DocItemLabel.TEXT)
            cambios.append(f"texto: {texto}")
    return cambios


def separar_subsecciones(doc: DoclingDocument) -> list[str]:
    """Saca como título propio las subsecciones que el PDF escribe dentro del párrafo."""
    cambios = []
    parrafos = [e for e, _ in doc.iterate_items()
                if isinstance(e, TextItem) and e.label == DocItemLabel.TEXT]
    for parrafo in parrafos:
        coincide = SUBSECCION_EN_LINEA.match(parrafo.text)
        if not coincide:
            continue
        titulo, resto = coincide.group(1), coincide.group(2)
        # Título nuevo justo antes del párrafo (after=False), en la misma página
        doc.insert_heading(sibling=parrafo, text=titulo, prov=parrafo.prov[0], after=False)
        # El párrafo se queda solo con su contenido, ya sin el título
        parrafo.text = resto
        parrafo.orig = resto
        cambios.append(titulo)
    # "2.2.2 Retrieval." sí salió como título, pero con punto final: se le quita para que
    # quede igual que las otras tres subsecciones
    for elemento, _ in doc.iterate_items():
        if isinstance(elemento, SectionHeaderItem) and elemento.text.endswith("."):
            elemento.text = elemento.text.rstrip(".")
    return cambios


def es_subseccion_en_lista(elemento) -> bool:
    """Dice si un elemento de lista es en realidad una subsección: marcador "2.1.1" y nombre."""
    return (isinstance(elemento, ListItem) and bool(MARCADOR_SUBSECCION.match(elemento.marker))
            and bool(NOMBRE_Y_RESTO.match(elemento.text)))


def separar_subsecciones_en_lista(doc: DoclingDocument) -> list[str]:
    """Convierte en títulos las subsecciones que Docling leyó como una lista numerada."""
    cambios = []
    # Se copia la lista de grupos porque se van a borrar algunos
    for grupo in list(doc.groups):
        elementos = [hijo.resolve(doc) for hijo in grupo.children]
        # Solo las listas cuyos elementos son todos subsecciones
        if not elementos or not all(es_subseccion_en_lista(e) for e in elementos):
            continue
        for elemento in elementos:
            nombre, resto = NOMBRE_Y_RESTO.match(elemento.text).groups()
            titulo = f"{elemento.marker} {nombre}"
            # Cada inserción va justo antes de la lista, así que quedan en orden:
            # título, texto, título, texto...
            doc.insert_heading(sibling=grupo, text=titulo, prov=elemento.prov[0], after=False)
            doc.insert_text(sibling=grupo, label=DocItemLabel.TEXT, text=resto,
                            prov=elemento.prov[0], after=False)
            cambios.append(titulo)
        # Borrar la lista borra también sus elementos, que ya se copiaron arriba
        doc.delete_items(node_items=[grupo])
    return cambios


def texto_de_tabla(tabla: TableItem) -> str:
    """Une las celdas de una tabla fila por fila, en un solo texto."""
    return " ".join(" ".join(c.text for c in fila if c.text) for fila in tabla.data.grid)


def armar_algoritmos(doc: DoclingDocument) -> list[str]:
    """Junta cada algoritmo en un solo bloque de código y los pone al final de su sección.

    El PDF imprime cada algoritmo a la mitad de un párrafo y Docling lo deja en tres piezas:
    el pie ("ALGORITHM 1: ..."), la línea "Require: ..." y el cuerpo (código, o una tabla en
    el algoritmo 2).
    """
    cambios = []
    elementos = [e for e, _ in doc.iterate_items()]
    titulos = [e for e in elementos if isinstance(e, SectionHeaderItem)]
    posicion = next(i for i, t in enumerate(titulos) if t.text == SECCION_ALGORITMOS)
    # Los algoritmos se insertan justo antes del título que sigue a 4.3 ("5 Post-Retrieval")
    destino = titulos[posicion + 1]
    for i, pie in enumerate(elementos):
        if pie.label != DocItemLabel.CAPTION or not pie.text.startswith(INICIO_ALGORITMO):
            continue
        requisitos, cuerpo = elementos[i + 1], elementos[i + 2]
        # Solo si las tres piezas son las esperadas
        if not requisitos.text.startswith("Require:") or cuerpo.label not in (
            DocItemLabel.CODE, DocItemLabel.TABLE
        ):
            continue
        texto_cuerpo = texto_de_tabla(cuerpo) if isinstance(cuerpo, TableItem) else cuerpo.text
        # Un paso por renglón (solo en el cuerpo: el "1:" de "ALGORITHM 1:" no es un paso)
        pasos = PASO_ALGORITMO.sub("\n", f"{requisitos.text} {texto_cuerpo}")
        # El PDF partió los subíndices "final" y "feedback": "yf inal" -> "yfinal"
        pasos = pasos.replace("f inal", "final").replace("f eedback", "feedback")
        texto = f"{pie.text}\n{pasos}"
        # Se conserva la página donde está impreso, para poder citarlo
        doc.insert_code(sibling=destino, text=texto, prov=pie.prov[0], after=False)
        doc.delete_items(node_items=[pie, requisitos, cuerpo])
        cambios.append(f"p.{pie.prov[0].page_no} {pie.text}")
    return cambios


def separar_nota_tabla_3(doc: DoclingDocument) -> bool:
    """Saca la nota de la Tabla 3 del párrafo donde quedó y la pone después de la tabla."""
    parrafo = next((e for e in doc.texts if INICIO_NOTA_TABLA_3 in e.text), None)
    if parrafo is None:
        return False
    corte = parrafo.text.index(INICIO_NOTA_TABLA_3)
    # A la palomita le faltaba el espacio que la separa de la palabra siguiente
    nota = parrafo.text[corte:].replace("✓in", "✓ in")
    # El párrafo se queda con lo de antes de la nota: termina en "as retrievers," y sigue
    # después de la tabla (eso lo une unir_parrafos_partidos)
    parrafo.text = parrafo.text[:corte].rstrip()
    parrafo.orig = parrafo.text
    tabla = next(t for t in doc.tables if t.prov[0].page_no == PAGINA_TABLA_3)
    doc.insert_text(sibling=tabla, label=DocItemLabel.FOOTNOTE, text=nota, prov=tabla.prov[0])
    return True


def es_continuacion(texto: str) -> bool:
    """Dice si un texto sigue una oración anterior: empieza en minúscula, "[" o "("."""
    return texto[:1].islower() or texto[:1] in "[("


def unir_parrafos_partidos(doc: DoclingDocument) -> list[str]:
    """Une los párrafos que el PDF cortó con una tabla, un algoritmo o un cambio de página."""
    # "(Continued)" marca que la Tabla 4 sigue en la otra página: no es contenido
    for elemento in doc.texts:
        if elemento.text == "(Continued)":
            elemento.content_layer = ContentLayer.FURNITURE
    cambios = []
    elementos = [e for e, _ in doc.iterate_items()]
    unidos = set()
    for i, parrafo in enumerate(elementos):
        if (parrafo.label != DocItemLabel.TEXT or id(parrafo) in unidos
                or parrafo.text.endswith(FIN_DE_ORACION)):
            continue
        # Busca el siguiente texto, saltando tablas, figuras, pies, notas y código
        j = i + 1
        while j < len(elementos) and elementos[j].label in FLOTANTES:
            j += 1
        if j == len(elementos):
            continue
        siguiente = elementos[j]
        if siguiente.label != DocItemLabel.TEXT or not es_continuacion(siguiente.text):
            continue
        paginas = f"p.{parrafo.prov[0].page_no}→{siguiente.prov[0].page_no}"
        cambios.append(f"{paginas}: ...{parrafo.text[-30:]} + {siguiente.text[:30]}...")
        # El párrafo se queda con las dos partes y con las páginas de ambas
        parrafo.text = f"{parrafo.text} {siguiente.text}"
        parrafo.orig = f"{parrafo.orig} {siguiente.orig}"
        parrafo.prov.extend(siguiente.prov)
        doc.delete_items(node_items=[siguiente])
        unidos.add(id(siguiente))
    return cambios


def asignar_niveles(doc: DoclingDocument) -> None:
    """Da a cada sección su nivel según su numeración: "2" -> 1, "2.1" -> 2, "2.2.1" -> 3."""
    for elemento, _ in doc.iterate_items():
        if isinstance(elemento, SectionHeaderItem):
            numero = NUMERACION.match(elemento.text)
            # Sin número ("Acknowledgments", "References"): nivel 1, como una sección principal
            elemento.level = len(numero.group(1).split(".")) if numero else 1


def pagina(elemento) -> int | str:
    """Número de página donde aparece un elemento del documento."""
    return elemento.prov[0].page_no if elemento.prov else "?"


def main() -> None:
    doc = DoclingDocument.load_from_json(ENTRADA)

    # El orden importa: primero el texto, para que las búsquedas de abajo vean letras normales
    print(f"Textos y celdas corregidos: {limpiar_textos(doc)}")
    print("\nTítulos falsos corregidos:")
    for cambio in corregir_titulos_falsos(doc):
        print(f"  {cambio}")
    print("\nSubsecciones separadas de su párrafo:")
    for cambio in separar_subsecciones(doc) + separar_subsecciones_en_lista(doc):
        print(f"  {cambio}")
    print(f"\nAlgoritmos juntados al final de {SECCION_ALGORITMOS}:")
    for cambio in armar_algoritmos(doc):
        print(f"  {cambio}")
    print(f"\nNota de la Tabla 3 separada de su párrafo: {separar_nota_tabla_3(doc)}")
    # Va después de los algoritmos y la nota: ya sin ellos, las mitades de cada párrafo
    # quedan una junto a la otra
    print("\nPárrafos partidos que se unieron:")
    for cambio in unir_parrafos_partidos(doc):
        print(f"  {cambio}")
    asignar_niveles(doc)

    # Las imágenes ya están en articulo_artifacts/: se reutilizan en vez de copiarlas
    doc.save_as_json(SALIDA, artifacts_dir=PROCESADO / "articulo_artifacts",
                     image_mode=ImageRefMode.REFERENCED)

    # Comprobaciones: el título, las secciones con su nivel y que no quede nada ilegible
    titulo = next(e for e, _ in doc.iterate_items() if isinstance(e, TitleItem))
    print(f"\nTítulo del documento: {titulo.text}")
    print("\nSecciones (página, nivel, título):")
    for elemento, _ in doc.iterate_items():
        if isinstance(elemento, SectionHeaderItem):
            sangria = "  " * elemento.level
            print(f"  p.{pagina(elemento):<3} n{elemento.level}{sangria}{elemento.text}")

    celdas = [c.text for t in doc.tables for c in t.data.table_cells]
    todo = "".join(e.text for e in doc.texts) + "".join(celdas)
    print(f"\nCaracteres ilegibles que quedan: {todo.count(ILEGIBLE)}")
    print(f"Palomitas: {todo.count(PALOMITA)}")
    print(f"\nGuardado en {SALIDA}")


if __name__ == "__main__":
    main()
