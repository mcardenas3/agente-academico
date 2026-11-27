"""Evaluación del agente completo: 25 preguntas con criterios explícitos y Claude como juez.

Uso (desde la carpeta api, con opensearch, vllm y ollama encendidos; tarda ~5 minutos):
    uv run --env-file ../.env python ../evaluacion/evaluar_agente.py
    uv run --env-file ../.env python ../evaluacion/evaluar_agente.py --solo-auditoria
      (vuelve a auditar lo ya guardado, sin correr el agente)

Preguntas:
- 15 conceptuales en español y 5 de término exacto en inglés: el artículo SÍ las responde, y se
  sabe de antemano qué fragmentos las contienen (los mismos de la evaluación de la búsqueda).
- 5 que el artículo NO responde: el agente debe decir que no lo cubre en vez de inventar.

Criterios por pregunta (se calculan solos):
- buscó: llamó la tool de búsqueda antes de responder.
- decisión correcta: dijo que se puede responder si y solo si el artículo lo cubre.
- cita correcta: entre sus citas está al menos un fragmento que de verdad responde la pregunta.
- idioma correcto: respondió en el idioma de la pregunta.
El agente corre igual que en la API: con los candados de revisar_respuesta y la verificación de
Claude antes de entregar (si Claude encuentra algo sin respaldo, el agente corrige). El veredicto
final de esa verificación queda como juez de las 25 (también las abstenciones):
- veredicto: aprobada / con observaciones / reprobada.
- fidelidad: todo lo que afirma la respuesta está en los fragmentos (fiel / parcial / no fiel).
- relevancia: qué tanto responde lo que se preguntó (1 a 5).
- citas correctas y si acertó en responder o abstenerse.
Ojo: ese juez es el mismo que guió la corrección, así que los criterios calculados solos son la
medida independiente. La corrida de antes de los candados quedó en
resultados_agente_sin_candados.json, para comparar.

Guarda todo en evaluacion/resultados_agente.json y muestra la tabla y el resumen.
"""

import asyncio
import json
import sys
from pathlib import Path

CARPETA = Path(__file__).resolve().parent
sys.path.insert(0, str(CARPETA.parent / "api"))

from agente import RespuestaCitada, crear_cliente, idioma_de, preguntar  # noqa: E402
from auditor import ACTIVO, MODELO_AUDITOR, Auditoria, AuditoriaFallida, Caso, auditar  # noqa: E402
from busqueda import traer_fragmentos  # noqa: E402

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


def auditar_fila(cliente, fila: dict, ya: Auditoria | None = None) -> dict:
    """Agrega a la fila el veredicto de Claude: el de la verificación (ya) o uno nuevo."""
    a = ya
    if a is None:
        ids = fila.get("recuperados") or fila["citas"]
        caso = Caso(pregunta=fila["pregunta"], respuesta=fila["respuesta"],
                    se_puede_responder=respondio(fila), citados=fila["citas"])
        try:
            a = asyncio.run(auditar(caso, traer_fragmentos(cliente, ids) if ids else []))
        except AuditoriaFallida as error:
            print(f"    no se pudo auditar: {error}", flush=True)
            return fila
    return fila | {
        "auditoria_veredicto": a.veredicto, "auditoria_fidelidad": a.fidelidad,
        "auditoria_relevancia": a.relevancia, "auditoria_citas_correctas": a.citas_correctas,
        "auditoria_abstencion_correcta": a.abstencion_correcta,
        "auditoria_problemas": a.problemas, "auditoria_analisis": a.analisis,
        "auditoria_costo_usd": a.costo_usd, "auditoria_segundos": a.segundos,
    }


def respondio(fila: dict) -> bool:
    """Si el agente dijo que el artículo sí permite responder."""
    if "respondio" in fila:
        return fila["respondio"]
    # Filas guardadas antes de este campo: se deduce de la decisión
    return fila["decision_correcta"] == (fila["tipo"] != "no responde")


def main() -> None:
    if not ACTIVO:
        sys.exit("Falta CLAUDE_API_KEY: corre con  uv run --env-file ../.env python …")
    cliente = crear_cliente()
    salida = CARPETA / "resultados_agente.json"
    if "--solo-auditoria" in sys.argv:
        guardado = json.loads(salida.read_text(encoding="utf-8"))["preguntas"]
        resultados = [auditar_fila(cliente, f) for f in guardado]
        for f in resultados:
            print(f"{f['n']:>2}. {f.get('auditoria_veredicto', '-')} | {f['pregunta'][:60]}",
                  flush=True)
        resumir(resultados, salida)
        return

    resultados = []
    for k, (pregunta, relevantes) in enumerate(PREGUNTAS, start=1):
        r: RespuestaCitada = preguntar(cliente, pregunta, verificar=True)
        citados = [c.id for c in r.citas]
        esperados = {f"{PREFIJO}{n:03d}" for n in relevantes}
        fila = {
            "n": k,
            "pregunta": pregunta,
            "tipo": "no responde" if not relevantes else ("término (en)" if k > 15 else "conceptual (es)"),
            "respuesta": r.respuesta,
            "respondio": r.se_puede_responder,
            "citas": citados,
            "recuperados": r.recuperados,
            "fragmentos_correctos": sorted(esperados),
            "busco": "buscar_en_articulo" in r.tools_llamadas,
            "decision_correcta": r.se_puede_responder == bool(relevantes),
            "cita_correcta": bool(esperados & set(citados)) if relevantes else None,
            "idioma_correcto": idioma_de(r.respuesta) == idioma_de(pregunta),
            "segundos": r.segundos,
            "tokens_entrada": r.tokens_entrada,
            "tokens_salida": r.tokens_salida,
            "error": r.error,
            "verificacion": r.verificacion,
            "correcciones": r.correcciones,
            "descartada": r.descartada,
        }
        fila = auditar_fila(cliente, fila, r.auditoria)
        resultados.append(fila)
        print(f"{k:>2}. {'✓' if fila['decision_correcta'] else '✗'} decisión  "
              f"cita={fila['cita_correcta']}  idioma={'✓' if fila['idioma_correcto'] else '✗'}  "
              f"claude={fila.get('auditoria_veredicto', '-')}/{fila.get('auditoria_relevancia', '-')}"
              f"  correcciones={r.correcciones}  {r.segundos:.1f}s | {pregunta[:45]}", flush=True)
    resumir(resultados, salida)


def resumir(resultados: list[dict], salida: Path) -> None:
    """Calcula el resumen, lo muestra y guarda todo en el JSON."""
    def porcentaje(filas, campo):
        valores = [f[campo] for f in filas if f[campo] is not None]
        return f"{sum(valores)}/{len(valores)}" if valores else "—"

    responde = [f for f in resultados if f["tipo"] != "no responde"]
    no_responde = [f for f in resultados if f["tipo"] == "no responde"]
    auditadas = [f for f in resultados if "auditoria_veredicto" in f]
    n = len(auditadas)

    def cuenta(campo, valor=True):
        return f"{sum(f[campo] == valor for f in auditadas)}/{n}"

    resumen = {
        "preguntas": len(resultados),
        "buscó": porcentaje(resultados, "busco"),
        "decisión correcta (todas)": porcentaje(resultados, "decision_correcta"),
        "respondió las que sí cubre": porcentaje(responde, "decision_correcta"),
        "se abstuvo en las que no cubre": porcentaje(no_responde, "decision_correcta"),
        "cita un fragmento correcto": porcentaje(responde, "cita_correcta"),
        "idioma correcto": porcentaje(resultados, "idioma_correcto"),
        f"claude ({MODELO_AUDITOR}): aprobadas": cuenta("auditoria_veredicto", "aprobada"),
        "claude: con observaciones": cuenta("auditoria_veredicto", "con_observaciones"),
        "claude: reprobadas": cuenta("auditoria_veredicto", "reprobada"),
        "claude: fiel": cuenta("auditoria_fidelidad", "fiel"),
        "claude: parcialmente fiel": cuenta("auditoria_fidelidad", "parcialmente_fiel"),
        "claude: citas correctas": cuenta("auditoria_citas_correctas"),
        "claude: acertó en responder/abstenerse": cuenta("auditoria_abstencion_correcta"),
        "claude: relevancia promedio": round(sum(f["auditoria_relevancia"] for f in auditadas)
                                             / max(n, 1), 2),
        "claude: costo total (US$)": round(sum(f["auditoria_costo_usd"] for f in auditadas), 4),
        "corregidas antes de mostrarse": f"{sum(f.get('correcciones', 0) > 0 for f in resultados)}"
                                         f"/{len(resultados)}",
        "descartadas (no fieles aun corregidas)": f"{sum(bool(f.get('descartada')) for f in resultados)}"
                                                  f"/{len(resultados)}",
        "segundos promedio": round(sum(f["segundos"] for f in resultados) / len(resultados), 2),
        "tokens de entrada promedio": round(sum(f["tokens_entrada"] for f in resultados)
                                            / len(resultados)),
    }
    print("\nResumen")
    for clave, valor in resumen.items():
        print(f"  {clave:<44}{valor}")

    salida.write_text(json.dumps({"resumen": resumen, "preguntas": resultados},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nGuardado en {salida}")


if __name__ == "__main__":
    main()
