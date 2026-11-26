"""Evaluación del agente completo: 25 preguntas con criterios explícitos y un LLM como juez.

Uso (desde la carpeta api, con opensearch, vllm y ollama encendidos; tarda ~5 minutos):
    uv run python ../evaluacion/evaluar_agente.py

Preguntas:
- 15 conceptuales en español y 5 de término exacto en inglés: el artículo SÍ las responde, y se
  sabe de antemano qué fragmentos las contienen (los mismos de la evaluación de la búsqueda).
- 5 que el artículo NO responde: el agente debe decir que no lo cubre en vez de inventar.

Criterios por pregunta (se calculan solos):
- buscó: llamó la tool de búsqueda antes de responder.
- decisión correcta: dijo que se puede responder si y solo si el artículo lo cubre.
- cita correcta: entre sus citas está al menos un fragmento que de verdad responde la pregunta.
- idioma correcto: respondió en el idioma de la pregunta.
Y un juez (el mismo LLM, en una llamada aparte, con los fragmentos citados a la vista):
- fiel: todo lo que afirma la respuesta está en los fragmentos citados (sí / parcial / no).
- relevante: qué tanto responde lo que se preguntó (1 a 5).

Guarda todo en evaluacion/resultados_agente.json y muestra la tabla y el resumen.
"""

import json
import sys
import time
from pathlib import Path
from typing import Literal

CARPETA = Path(__file__).resolve().parent
sys.path.insert(0, str(CARPETA.parent / "api"))

from pydantic import BaseModel, Field  # noqa: E402
from pydantic_ai import Agent, ModelSettings, ToolOutput  # noqa: E402

from agente import crear_cliente, idioma_de, modelo, preguntar  # noqa: E402
from busqueda import INDICE  # noqa: E402

PREFIJO = "huang2026-rag-survey-"

# (pregunta, números de los fragmentos que la responden; vacío = el artículo no la responde)
PREGUNTAS: list[tuple[str, list[int]]] = [
    # 15 conceptuales, en español
    ("¿Por qué alucinan los modelos de lenguaje y cómo ayuda RAG?", [5]),
    ("¿Cuáles son las fases del paradigma RAG?", [10]),
    ("¿En qué se diferencian BM25 y los recuperadores basados en modelos de lenguaje?", [8]),
    ("¿Cómo funciona el hashing sensible a la localidad para buscar vecinos cercanos?", [17]),
    ("¿Qué estrategias existen para modificar la consulta del usuario?", [12, 20]),
    ("¿Qué es la reformulación de consultas y qué métodos la usan?", [21]),
    ("¿Qué diferencia hay entre la recuperación iterativa y la recursiva?", [25, 26, 27]),
    ("En la recuperación adaptativa, ¿cómo decide el modelo cuándo recuperar más información?",
     [28, 32]),
    ("¿Cómo funcionan los modelos supervisados de reordenamiento?", [34]),
    ("¿Para qué sirve filtrar los documentos recuperados?", [36, 37]),
    ("¿Cómo se combinan varios documentos recuperados al generar la respuesta?", [39]),
    ("¿Cómo se personalizan las respuestas para cada usuario?", [41, 42]),
    ("¿Qué métricas y conjuntos de datos se usan para evaluar RAG?", [43]),
    ("¿Cómo se comprime el contexto para que la generación sea más rápida?", [69]),
    ("¿Más documentos en un contexto largo siempre mejoran la respuesta?", [71]),
    # 5 de término exacto, en inglés
    ("What is HyDE and how does it improve retrieval?", [66]),
    ("How does RAPTOR organize documents for retrieval?", [27]),
    ("Which strategies does FILCO use to filter context?", [37]),
    ("How does the Chain-of-Verification (CoVe) method work?", [40]),
    ("What is the SELF-ROUTE method?", [73]),
    # 5 que el artículo no responde
    ("¿Cuál es la capital de Francia?", []),
    ("¿Qué resultados obtiene GPT-7 en el benchmark MMLU según el artículo?", []),
    ("¿Cuántos parámetros tiene el modelo que proponen los autores?", []),
    ("¿Cómo se entrena una red neuronal convolucional para clasificar imágenes?", []),
    ("¿Qué opinan los autores sobre las criptomonedas?", []),
]


class Veredicto(BaseModel):
    """Evaluation of an answer against the article fragments it cites."""

    # El motivo va primero para que el modelo compare antes de elegir el veredicto
    motivo: str = Field(description="First, compare the answer with the fragments in one or two "
                                    "sentences.")
    fiel: Literal["si", "parcial", "no"] = Field(
        description="'si' if every claim in the answer is supported by the fragments, 'parcial' "
                    "if some claims are not, 'no' if the answer contradicts or ignores them.")
    relevancia: int = Field(ge=1, le=5, description="How well the answer addresses the question "
                                                    "(1 = not at all, 5 = completely).")


# Juez: el mismo modelo (es el único que cabe en la GPU junto con los embeddings). Entrega su
# veredicto con una tool llamada "calificar", igual que el agente responde con "responder".
# Medido: con salida JSON obligatoria (NativeOutput) calificó "no" y 1 las 20 respuestas, aunque
# en su motivo dijera que estaban respaldadas; el formato forzado le quita la capacidad de razonar
juez = Agent(
    modelo,
    output_type=ToolOutput(Veredicto, name="calificar",
                           description="Give your verdict on the answer."),
    instructions="You are a strict evaluator of question answering over a scientific article. "
                 "Judge ONLY with the fragments given; do not use outside knowledge. Give your "
                 "verdict ONLY by calling the tool `calificar`.",
    model_settings=ModelSettings(temperature=0, extra_body={"reasoning_effort": "none"}),
    retries=2,
)


def textos_de(cliente, ids: list[str]) -> str:
    """Texto de los fragmentos citados, para dárselo al juez."""
    if not ids:
        return "(no fragments cited)"
    docs = cliente.mget(index=INDICE, body={"ids": ids}, _source=["seccion", "texto"])["docs"]
    return "\n\n".join(f"[{d['_id']}] {d['_source']['seccion']}\n{d['_source']['texto']}"
                       for d in docs if d.get("found"))


def juzgar(cliente, fila: dict) -> dict:
    """Le pide al juez su veredicto sobre una respuesta y lo agrega a la fila."""
    inicio = time.perf_counter()
    v = juez.run_sync(
        f"Question: {fila['pregunta']}\n\nAnswer to evaluate: {fila['respuesta']}\n\n"
        f"Article fragments cited by the answer:\n{textos_de(cliente, fila['citas'])}").output
    return fila | {"juez_fiel": v.fiel, "juez_relevancia": v.relevancia, "juez_motivo": v.motivo,
                   "juez_segundos": round(time.perf_counter() - inicio, 2)}


def main() -> None:
    cliente = crear_cliente()
    salida = CARPETA / "resultados_agente.json"
    # Con --solo-juez se vuelve a juzgar lo ya guardado, sin volver a correr el agente
    if "--solo-juez" in sys.argv:
        guardado = json.loads(salida.read_text(encoding="utf-8"))["preguntas"]
        resultados = [juzgar(cliente, f) if "juez_fiel" in f else f for f in guardado]
        for f in resultados:
            print(f"{f['n']:>2}. juez={f.get('juez_fiel', '-')}/{f.get('juez_relevancia', '-')} | "
                  f"{f['pregunta'][:60]}", flush=True)
        resumir(resultados, salida)
        return

    resultados = []
    for k, (pregunta, relevantes) in enumerate(PREGUNTAS, start=1):
        r = preguntar(cliente, pregunta)
        citados = [c.id for c in r.citas]
        esperados = {f"{PREFIJO}{n:03d}" for n in relevantes}
        fila = {
            "n": k,
            "pregunta": pregunta,
            "tipo": "no responde" if not relevantes else ("término (en)" if k > 15 else "conceptual (es)"),
            "respuesta": r.respuesta,
            "citas": citados,
            "fragmentos_correctos": sorted(esperados),
            "busco": "buscar_en_articulo" in r.tools_llamadas,
            "decision_correcta": r.se_puede_responder == bool(relevantes),
            "cita_correcta": bool(esperados & set(citados)) if relevantes else None,
            "idioma_correcto": idioma_de(r.respuesta) == idioma_de(pregunta),
            "segundos": r.segundos,
            "tokens_entrada": r.tokens_entrada,
            "tokens_salida": r.tokens_salida,
            "error": r.error,
        }
        # El juez solo califica las que el artículo responde y el agente sí respondió
        if relevantes and r.se_puede_responder:
            fila = juzgar(cliente, fila)
        resultados.append(fila)
        print(f"{k:>2}. {'✓' if fila['decision_correcta'] else '✗'} decisión  "
              f"cita={fila['cita_correcta']}  idioma={'✓' if fila['idioma_correcto'] else '✗'}  "
              f"juez={fila.get('juez_fiel', '-')}/{fila.get('juez_relevancia', '-')}  "
              f"{r.segundos:.1f}s | {pregunta[:55]}", flush=True)
    resumir(resultados, salida)


def resumir(resultados: list[dict], salida: Path) -> None:
    """Calcula el resumen, lo muestra y guarda todo en el JSON."""
    def porcentaje(filas, campo):
        valores = [f[campo] for f in filas if f[campo] is not None]
        return f"{sum(valores)}/{len(valores)}" if valores else "—"

    responde = [f for f in resultados if f["tipo"] != "no responde"]
    no_responde = [f for f in resultados if f["tipo"] == "no responde"]
    juzgadas = [f for f in resultados if "juez_fiel" in f]
    resumen = {
        "preguntas": len(resultados),
        "buscó": porcentaje(resultados, "busco"),
        "decisión correcta (todas)": porcentaje(resultados, "decision_correcta"),
        "respondió las que sí cubre": porcentaje(responde, "decision_correcta"),
        "se abstuvo en las que no cubre": porcentaje(no_responde, "decision_correcta"),
        "cita un fragmento correcto": porcentaje(responde, "cita_correcta"),
        "idioma correcto": porcentaje(resultados, "idioma_correcto"),
        "juez: fiel (sí)": f"{sum(f['juez_fiel'] == 'si' for f in juzgadas)}/{len(juzgadas)}",
        "juez: fiel (parcial)": f"{sum(f['juez_fiel'] == 'parcial' for f in juzgadas)}/{len(juzgadas)}",
        "juez: relevancia promedio": round(sum(f["juez_relevancia"] for f in juzgadas)
                                           / max(len(juzgadas), 1), 2),
        "segundos promedio": round(sum(f["segundos"] for f in resultados) / len(resultados), 2),
        "tokens de entrada promedio": round(sum(f["tokens_entrada"] for f in resultados)
                                            / len(resultados)),
    }
    print("\nResumen")
    for clave, valor in resumen.items():
        print(f"  {clave:<34}{valor}")

    salida.write_text(json.dumps({"resumen": resumen, "preguntas": resultados},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nGuardado en {salida}")


if __name__ == "__main__":
    main()
