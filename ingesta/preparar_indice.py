"""Deja listo el índice de OpenSearch: si ya tiene todos los fragmentos no hace nada; si no, lo
crea y lo llena. Lo corre la API de Docker al arrancar, así un clon nuevo del repositorio queda
funcionando con un solo "docker compose up".

Uso manual (desde la carpeta ingesta, con opensearch y vllm encendidos):
    uv run python preparar_indice.py

No corre Docling: parte de datos/procesado/fragmentos.jsonl, que sí se sube al repositorio. Para
regenerar los fragmentos desde el PDF: convertir.py → limpiar.py → fragmentar.py.
"""

import sys
import time

from opensearchpy import OpenSearch
from opensearchpy.exceptions import ConnectionError as ErrorDeConexion

import indexar
from crear_indice import CONFIGURACION, FRAGMENTOS, INDICE, URL_OPENSEARCH

# Cuánto esperar a OpenSearch si todavía está arrancando
INTENTOS, ESPERA = 30, 5


def main() -> None:
    with FRAGMENTOS.open(encoding="utf-8") as archivo:
        esperados = sum(1 for linea in archivo if linea.strip())

    cliente = OpenSearch(hosts=[URL_OPENSEARCH])
    for intento in range(INTENTOS):
        try:
            cliente.info()
            break
        except ErrorDeConexion:
            print(f"OpenSearch todavía no responde ({intento + 1}/{INTENTOS})", flush=True)
            time.sleep(ESPERA)
    else:
        sys.exit("OpenSearch no respondió")

    if cliente.indices.exists(index=INDICE):
        hay = cliente.count(index=INDICE)["count"]
        if hay == esperados:
            print(f"Índice {INDICE} listo: {hay} fragmentos")
            return
        print(f"El índice tiene {hay} de {esperados} fragmentos: se vuelve a llenar")
    else:
        cliente.indices.create(index=INDICE, body=CONFIGURACION)
        print(f"Índice {INDICE} creado")
    indexar.main()  # vectoriza con vLLM y guarda; reemplaza en vez de duplicar


if __name__ == "__main__":
    main()
