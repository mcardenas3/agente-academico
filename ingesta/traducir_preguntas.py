"""Traduce al inglés las preguntas en español de la evaluación, con el LLM de Ollama.

Uso (desde la carpeta ingesta, con ollama encendido):  uv run python traducir_preguntas.py

BM25 compara palabras y el artículo está en inglés, así que con la pregunta en español casi no
encuentra nada. Este script traduce cada pregunta una vez y guarda el resultado en
datos/evaluacion/traducciones.json; evaluar_busqueda.py lo lee para la parte BM25. Así la
evaluación mide la búsqueda con una traducción fija y revisada, y no depende de Ollama.
"""

import json
import time

import httpx

# PREGUNTAS: las preguntas de la evaluación. TRADUCCIONES: el archivo de salida (se sube a
# GitHub, porque es parte de la evidencia de la evaluación); su ruta se define en
# evaluar_busqueda.py, que es quien lo lee
from evaluar_busqueda import PREGUNTAS, TRADUCCIONES

# Dirección de Ollama (solo escucha en esta computadora).
# 127.0.0.1 y no "localhost": en Windows, Python prueba primero ::1 (IPv6), donde Docker no
# escucha, y espera todo el timeout antes de pasar a 127.0.0.1
URL_OLLAMA = "http://127.0.0.1:11434/api/chat"
# El LLM del proyecto; siempre con ":4b" (sin etiqueta, Ollama bajaría el de 30b)
MODELO_LLM = "nemotron-3-nano:4b"

# Instrucción para el modelo: traducir con la terminología del tema, no responder la pregunta y
# dejar los nombres intactos. Sin la frase del tema, "recuperación" salía como "recovery" en vez
# de "retrieval" (4 de 25 preguntas), y BM25 no lo relaciona con el texto del artículo
INSTRUCCION = (
    "Translate the user question from Spanish into English. The questions are about "
    "information retrieval and retrieval-augmented generation (RAG), so use the standard "
    "English terminology of that field. Keep technical terms and method names (for example "
    "RAPTOR, HyDE, FILCO) exactly as written. Do not answer the question. "
    "Reply with the translation only."
)


def traducir(cliente_http: httpx.Client, pregunta: str) -> str:
    """Le pide a Ollama la traducción al inglés de una pregunta."""
    respuesta = cliente_http.post(URL_OLLAMA, json={
        "model": MODELO_LLM,
        "messages": [
            {"role": "system", "content": INSTRUCCION},
            {"role": "user", "content": pregunta},
        ],
        # La respuesta completa de una vez, no palabra por palabra
        "stream": False,
        # Sin "pensar" antes de responder: para traducir no hace falta y tarda más
        "think": False,
        # Temperatura 0: siempre elige la palabra más probable, así la traducción no varía
        "options": {"temperature": 0},
    })
    respuesta.raise_for_status()
    # Se quitan espacios y saltos de línea sobrantes al inicio y al final
    return respuesta.json()["message"]["content"].strip()


def main() -> None:
    # La primera petición tarda más porque Ollama carga el modelo en la GPU (~20 s)
    traducciones = {}
    inicio = time.perf_counter()
    with httpx.Client(timeout=120) as cliente_http:
        for k, (ingles, espanol, _, _) in enumerate(PREGUNTAS, start=1):
            traducciones[espanol] = traducir(cliente_http, espanol)
            # Se muestran juntas la traducción y la pregunta en inglés escrita a mano, para
            # revisar a ojo que dicen lo mismo
            print(f"{k:>2}. {espanol}\n    LLM:  {traducciones[espanol]}\n    mano: {ingles}")
    print(f"\n{len(traducciones)} traducciones en {time.perf_counter() - inicio:.1f} s")

    # ensure_ascii=False deja los acentos legibles en el archivo; indent lo hace fácil de revisar
    TRADUCCIONES.parent.mkdir(parents=True, exist_ok=True)
    TRADUCCIONES.write_text(json.dumps(traducciones, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print(f"Guardadas en {TRADUCCIONES}")


if __name__ == "__main__":
    main()
