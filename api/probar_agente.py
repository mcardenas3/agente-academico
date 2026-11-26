"""Prueba del agente con preguntas de distintos tipos y una conversación con seguimiento.

Uso (desde la carpeta api, con opensearch, vllm y ollama encendidos):
    uv run python probar_agente.py
    uv run python probar_agente.py "¿Qué es RAPTOR?"     (una pregunta propia)

Por cada pregunta muestra cómo buscó el agente, la respuesta, si se puede responder, las citas
(sección y páginas) y lo que costó: llamadas al LLM, tokens y segundos.
"""

import sys

from agente import RespuestaCitada, Turno, crear_cliente, preguntar

# Preguntas sueltas, cada una con lo que se espera del agente
PREGUNTAS = [
    ("¿Qué es HyDE y cómo mejora la recuperación?", "término exacto, en español"),
    ("¿Cuáles son las fases del paradigma RAG?", "conceptual"),
    ("How does RAPTOR organize documents for retrieval?", "en inglés: debe responder en inglés"),
    ("¿Qué es REALM?", "debería traer la referencia [48]"),
    ("¿Cuál es la capital de Francia?", "fuera del artículo: mensaje fijo, sin citas"),
    ("¿Qué resultados obtiene GPT-7 en el benchmark MMLU según el artículo?",
     "suena del tema pero no está: no debe inventar"),
]

# Conversación: la segunda pregunta solo se entiende con la primera
CONVERSACION = [
    "¿Qué es el re-ranking en RAG?",
    "¿Y qué diferencia hay entre los supervisados y los no supervisados?",
]


def mostrar(pregunta: str, esperado: str, r: RespuestaCitada) -> None:
    print(f"\n=== {pregunta}\n    (se espera: {esperado})")
    print(f"tools: {r.tools_llamadas or 'NINGUNA'}  |  buscó: {r.consulta_en!r}")
    print(f"¿se puede responder?: {r.se_puede_responder}")
    print(f"respuesta: {r.respuesta}")
    for c in r.citas:
        print(f"  cita: {c.seccion}, p. {', '.join(map(str, c.paginas))}  ({c.id})")
    if r.error:
        print(f"  error: {r.error}")
    print(f"costo: {r.llamadas_llm} llamadas al LLM, {r.tokens_entrada} tokens de entrada, "
          f"{r.tokens_salida} de salida, {r.segundos} s ({r.segundos_busqueda} s buscando)")


def main() -> None:
    cliente = crear_cliente()
    if len(sys.argv) > 1:  # una pregunta escrita al correr el script
        mostrar(sys.argv[1], "pregunta propia", preguntar(cliente, sys.argv[1]))
        return

    for pregunta, esperado in PREGUNTAS:
        mostrar(pregunta, esperado, preguntar(cliente, pregunta))

    # La conversación: cada pregunta recibe los turnos anteriores
    turnos: list[Turno] = []
    for pregunta in CONVERSACION:
        r = preguntar(cliente, pregunta, turnos)
        mostrar(pregunta, "conversación (la 2.ª se reescribe sola)", r)
        turnos.append(Turno(pregunta=pregunta, respuesta=r.respuesta))


if __name__ == "__main__":
    main()
