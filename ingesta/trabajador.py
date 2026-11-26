"""Trabajador que procesa los artículos subidos desde el panel /admin.

Corre en su propio contenedor (servicio "trabajador" de docker-compose). La API guarda el PDF,
registra el artículo en PostgreSQL con estado "en_cola" y mete su id en una lista de Redis; este
proceso los saca uno por uno y hace lo mismo que la ingesta del artículo original:

    PDF → Docling → limpieza genérica → fragmentos de 512 tokens → vectores (vLLM) → OpenSearch

y va dejando en PostgreSQL en qué paso va, para que el panel lo muestre. Si el contenedor se
reinicia a la mitad, al arrancar vuelve a encolar lo que quedó pendiente.
"""

import os
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from docling.datamodel.base_models import ConversionStatus
from docling.datamodel.progress import PageCompletedProgress
from docling_core.types.doc import SectionHeaderItem, TitleItem
from opensearchpy import OpenSearch
from psycopg.rows import dict_row
from redis import Redis
from redis.exceptions import RedisError

from convertir import crear_convertidor
from crear_indice import INDICE, URL_OPENSEARCH
from fragmentar import crear_tokenizador, fragmentar_documento
from indexar import indexar_fragmentos
from limpiar import limpieza_generica

URL_BASE_DATOS = os.environ.get(
    "URL_BASE_DATOS", "postgresql://agente:agente@127.0.0.1:5432/agente_academico")
URL_REDIS = os.environ.get("URL_REDIS", "redis://127.0.0.1:6379/0")
# Donde la API guarda los PDFs subidos (un volumen de Docker compartido)
CARPETA_ARCHIVOS = Path(os.environ.get("CARPETA_ARCHIVOS", "/app/archivos"))
# Lista de Redis que hace de cola de trabajos (la API agrega a la derecha, aquí se saca por la izquierda)
COLA = "cola:articulos"
# Tope de páginas: un libro entero tardaría demasiado en CPU
MAX_PAGINAS = int(os.environ.get("MAX_PAGINAS", "80"))
# Hilos de CPU para Docling: 4 de 8 núcleos, para que la laptop siga respondiendo
HILOS = int(os.environ.get("HILOS_DOCLING", "4"))


def actualizar(bd: psycopg.Connection, articulo_id: str, **campos) -> None:
    """Escribe en la fila del artículo lo que se le pase (estado, progreso, error...)."""
    columnas = ", ".join(f"{nombre} = %s" for nombre in campos)
    bd.execute(f"UPDATE articulos SET {columnas} WHERE id = %s", (*campos.values(), articulo_id))


def titulo_del_documento(doc, respaldo: str) -> str:
    """El título que Docling encontró en el PDF; si no hay, el primer encabezado; si no, el nombre."""
    for elemento, _ in doc.iterate_items():
        if isinstance(elemento, TitleItem) and elemento.text.strip():
            return elemento.text.strip()
    for elemento, _ in doc.iterate_items():
        if isinstance(elemento, SectionHeaderItem) and len(elemento.text.strip()) > 15:
            return elemento.text.strip()
    return respaldo


def procesar(articulo_id: str, bd, redis: Redis, opensearch: OpenSearch, convertidor,
             tokenizador) -> None:
    fila = bd.execute("SELECT titulo, archivo FROM articulos WHERE id = %s",
                      (articulo_id,)).fetchone()
    if fila is None:  # se borró mientras esperaba en la cola
        return
    inicio = time.perf_counter()
    actualizar(bd, articulo_id, estado="procesando", error=None,
               progreso="Convirtiendo el PDF con Docling")

    def avance(evento) -> None:
        # Docling avisa cada página terminada: se refleja en el panel
        if isinstance(evento, PageCompletedProgress):
            actualizar(bd, articulo_id, progreso=f"Convirtiendo con Docling: página "
                       f"{evento.completed_pages} de {evento.total_pages}")

    resultado = convertidor.convert(CARPETA_ARCHIVOS / fila["archivo"], raises_on_error=False,
                                    max_num_pages=MAX_PAGINAS, progress_callback=avance)
    if resultado.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
        errores = "; ".join(str(e.error_message) for e in resultado.errors)[:300]
        raise ValueError(f"Docling no pudo convertir el PDF (¿más de {MAX_PAGINAS} páginas o "
                         f"archivo dañado?). {errores}".strip())
    doc = resultado.document

    actualizar(bd, articulo_id, progreso="Limpiando y partiendo en fragmentos")
    limpieza_generica(doc)
    titulo = fila["titulo"] or titulo_del_documento(doc, Path(fila["archivo"]).stem)
    fragmentos = fragmentar_documento(doc, articulo_id, titulo, None, tokenizador)
    if not any(f["tipo"] == "contenido" for f in fragmentos):
        raise ValueError("No se encontró texto en el PDF. Si es un escaneo (imágenes), "
                         "necesita OCR, que este proyecto no activa.")

    actualizar(bd, articulo_id, progreso=f"Vectorizando {len(fragmentos)} fragmentos")
    # Si se está reprocesando, primero se quitan sus fragmentos viejos
    opensearch.delete_by_query(index=INDICE, body={"query": {"term": {"articulo_id": articulo_id}}},
                               refresh=True)
    cargados, _, _ = indexar_fragmentos(opensearch, fragmentos, avisar=lambda _m: None)

    actualizar(bd, articulo_id, estado="listo", progreso=None, titulo=titulo,
               paginas=doc.num_pages(), fragmentos=cargados, procesado_en=datetime.now(UTC))
    # Las respuestas guardadas para "todos los artículos" ya no consideran este: se olvidan
    for llave in redis.scan_iter("respuesta:*"):
        redis.delete(llave)
    print(f"[{articulo_id}] listo: {doc.num_pages()} páginas, {cargados} fragmentos, "
          f"{time.perf_counter() - inicio:.0f} s", flush=True)


def esperar(nombre: str, prueba) -> None:
    """Reintenta hasta que un servicio responda (al arrancar todo a la vez puede tardar)."""
    for _ in range(60):
        try:
            prueba()
            return
        except Exception:
            time.sleep(5)
    raise SystemExit(f"{nombre} no respondió")


def main() -> None:
    # La espera de la cola es de 30 s (ver abajo); el socket espera un poco más que eso. Sin un
    # tope así, la espera indefinida terminaba en "Timeout reading from redis" y tumbaba al proceso
    redis = Redis.from_url(URL_REDIS, decode_responses=True, socket_timeout=60,
                           health_check_interval=30)
    opensearch = OpenSearch(hosts=[URL_OPENSEARCH])
    esperar("Redis", redis.ping)
    esperar("OpenSearch", opensearch.info)
    bd = psycopg.connect(URL_BASE_DATOS, autocommit=True, row_factory=dict_row)

    # Lo que quedó a medias si el contenedor se reinició: se vuelve a encolar, en orden
    pendientes = bd.execute("SELECT id FROM articulos WHERE estado IN ('en_cola', 'procesando') "
                            "ORDER BY subido_en").fetchall()
    redis.delete(COLA)
    for fila in pendientes:
        actualizar(bd, fila["id"], estado="en_cola", progreso="En la cola")
        redis.rpush(COLA, fila["id"])

    # Los modelos de Docling y el tokenizador se cargan una vez y se reusan en cada artículo
    convertidor = crear_convertidor(imagenes=False, hilos=HILOS)
    tokenizador = crear_tokenizador()
    print(f"Trabajador listo ({len(pendientes)} pendientes)", flush=True)

    while True:
        try:
            # Espera hasta 30 s a que haya un trabajo; si no hay, vuelve a esperar
            trabajo = redis.blpop([COLA], timeout=30)
        except RedisError as error:  # Redis se reinició o la conexión se cortó: se reintenta
            print(f"Redis no respondió ({error}); reintento en 5 s", flush=True)
            time.sleep(5)
            continue
        if trabajo is None:
            continue
        _, articulo_id = trabajo
        try:
            procesar(articulo_id, bd, redis, opensearch, convertidor, tokenizador)
        except Exception as error:
            traceback.print_exc()
            actualizar(bd, articulo_id, estado="error", progreso=None, error=str(error)[:500])


if __name__ == "__main__":
    main()
