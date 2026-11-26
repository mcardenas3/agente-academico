"""Prueba del agente con preguntas de distintos tipos.

Uso (desde la carpeta api, con opensearch, vllm y ollama encendidos):
    uv run python probar_agente.py
    uv run python probar_agente.py "¿Qué es RAPTOR?"     (una pregunta propia)

Por cada pregunta muestra qué tools pidió el LLM, la respuesta, si dijo que se puede responder,
las citas (sección y páginas), y lo que costó: llamadas al LLM, tokens y segundos.
"""

import sys

from agente import crear_cliente, preguntar

# Preguntas de prueba, cada una con lo que se espera del agente
PREGUNTAS = [
    ("¿Qué es HyDE y cómo mejora la recuperación?", "término exacto, en español"),
    ("¿Cuáles son las fases del paradigma RAG?", "conceptual"),
    ("How does RAPTOR organize documents for retrieval?", "en inglés: debe responder en inglés"),
    ("¿Qué es REALM?", "debería traer la referencia [48]"),
    ("¿Cuál es la capital de Francia?", "fuera del artículo: no se puede responder"),
    ("¿Qué resultados obtiene GPT-7 en el benchmark MMLU según el artículo?",
     "suena del tema pero no está: no debe inventar"),
]


def main() -> None:
    cliente = crear_cliente()
    # Con una pregunta escrita al correr el script, solo se prueba esa
    preguntas = [(sys.argv[1], "pregunta propia")] if len(sys.argv) > 1 else PREGUNTAS

    for pregunta, esperado in preguntas:
        r = preguntar(cliente, pregunta)
        print(f"\n=== {pregunta}\n    (se espera: {esperado})")
        print(f"tools: {r.tools_llamadas or 'NINGUNA'}")
        print(f"¿se puede responder?: {r.se_puede_responder}")
        print(f"respuesta: {r.respuesta}")
        for c in r.citas:
            print(f"  cita: {c.seccion}, p. {', '.join(map(str, c.paginas))}  ({c.id})")
        if r.ids_no_vistos:
            print(f"  ids citados que la búsqueda no dio: {r.ids_no_vistos}")
        print(f"costo: {r.llamadas_llm} llamadas al LLM, {r.tokens_entrada} tokens de entrada, "
              f"{r.tokens_salida} de salida, {r.segundos} s")


if __name__ == "__main__":
    main()
