"""Experimento: ¿quién traduce la pregunta al inglés para BM25?

Uso (desde la carpeta api, con opensearch, vllm y ollama encendidos; tarda ~3–6 minutos):
    uv run python ../experimentos/agente/probar_traduccion.py

Dos opciones:
- A. El agente: al llamar buscar_en_articulo escribe él mismo `consulta_en` (lo que hace hoy).
- B. Una llamada aparte a Ollama, solo para traducir, a temperatura 0 (traducir_preguntas.py de
  la Etapa 4, con su instrucción).
Con las 25 preguntas en español de la evaluación, corre el agente completo y guarda lo que pasó a
la tool, traduce también con B, y mide la búsqueda (MRR y Recall@5) con cada traducción. El vector
es siempre el de la pregunta original en español, así que solo cambia la parte de BM25. Como
referencia van la pregunta en inglés escrita a mano (techo) y lo que la tool recibió de verdad.
"""

import sys
import time
from pathlib import Path

import httpx

# El agente vive en api/; las preguntas, las métricas y la traducción B, en experimentos/ingesta/
RAIZ = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(RAIZ / "api"), str(RAIZ / "experimentos" / "ingesta")]
from agente import agente, crear_cliente, Dependencias  # noqa: E402
from busqueda import buscar_con_vector  # noqa: E402
from evaluar_busqueda import (  # noqa: E402
    PREFIJO_ID, PREGUNTAS, lugar_del_relevante, recall_5, vectorizar_preguntas,
)
from traducir_preguntas import traducir  # noqa: E402


def ids(fragmentos) -> list[int]:
    """Números de los fragmentos: "huang2026-rag-survey-034" → 34."""
    return [int(f.id.removeprefix(PREFIJO_ID)) for f in fragmentos]


def main() -> None:
    cliente = crear_cliente()
    espanol = [p[1] for p in PREGUNTAS]
    n = len(PREGUNTAS)

    # --- A: el agente completo; de su historial se lee qué le pasó a la tool en la 1.ª búsqueda
    consultas, consultas_en, segundos_a = [], [], []
    for pregunta in espanol:
        inicio = time.perf_counter()
        resultado = agente.run_sync(pregunta, deps=Dependencias(cliente=cliente))
        segundos_a.append(time.perf_counter() - inicio)
        llamada = next(p for m in resultado.new_messages() for p in m.parts
                       if p.part_kind == "tool-call" and p.tool_name == "buscar_en_articulo")
        argumentos = llamada.args_as_dict()
        consultas.append(argumentos["consulta"])
        consultas_en.append(argumentos["consulta_en"])

    # --- B: una llamada aparte por pregunta, midiendo cuánto agrega
    traducciones_b, segundos_b = [], []
    with httpx.Client(timeout=120) as cliente_http:
        for pregunta in espanol:
            inicio = time.perf_counter()
            traducciones_b.append(traducir(cliente_http, pregunta))
            segundos_b.append(time.perf_counter() - inicio)

    # Vectores: la pregunta original (como en las opciones A y B) y lo que la tool vectorizó de
    # verdad (`consulta`, que el agente podría haber cambiado)
    vectores = vectorizar_preguntas(espanol + consultas)
    vector_original, vector_tool = vectores[:n], vectores[n:]

    # Condición → (texto para BM25, vectores)
    condiciones = {
        "A: agente": (consultas_en, vector_original),
        "B: llamada aparte": (traducciones_b, vector_original),
        "Inglés a mano (techo)": ([p[0] for p in PREGUNTAS], vector_original),
        "Lo que recibió la tool": (consultas_en, vector_tool),
    }
    print(f"\n{'Condición':<24}{'MRR':>8}{'Rec@5':>8}")
    for nombre, (textos, vecs) in condiciones.items():
        resultados = [ids(buscar_con_vector(cliente, textos[k], vecs[k])) for k in range(n)]
        lugares = [lugar_del_relevante(r, p[2]) for r, p in zip(resultados, PREGUNTAS)]
        mrr = sum(1 / x for x in lugares if x) / n
        rec = sum(recall_5(r, p[2]) for r, p in zip(resultados, PREGUNTAS)) / n
        print(f"{nombre:<24}{mrr:>8.3f}{rec:>8.3f}")

    # Diferencias entre las dos traducciones, para revisarlas a ojo
    iguales = sum(a.strip().lower() == b.strip().lower() for a, b in zip(consultas_en, traducciones_b))
    print(f"\nTraducciones idénticas (sin contar mayúsculas): {iguales}/{n}")
    for k, (a, b) in enumerate(zip(consultas_en, traducciones_b), start=1):
        if a.strip().lower() != b.strip().lower():
            print(f"{k:>2}. A: {a}\n    B: {b}")

    cambiadas = [k + 1 for k in range(n) if consultas[k] != espanol[k]]
    print(f"\nPreguntas en que el agente NO pasó la pregunta original como `consulta`: "
          f"{cambiadas or 'ninguna'}")
    for k in cambiadas:
        print(f"{k:>2}. original: {espanol[k - 1]}\n    tool:     {consultas[k - 1]}")

    print(f"\nTiempo: agente completo {sum(segundos_a) / n:.1f} s por pregunta (incluye responder); "
          f"la llamada aparte de B agregaría {sum(segundos_b) / n:.2f} s por pregunta")


if __name__ == "__main__":
    main()
