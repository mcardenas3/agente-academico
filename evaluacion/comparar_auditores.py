"""Compara dos modelos de Claude como auditores: qué tanto aciertan contra cuánto cuestan.

Uso (desde la carpeta api, con OpenSearch encendido; no usa Ollama; ~US$0.40 con Sonnet):
    uv run --env-file ../.env python ../evaluacion/comparar_auditores.py
    uv run --env-file ../.env python ../evaluacion/comparar_auditores.py claude-haiku-5-5 claude-opus-5-5

Los dos modelos auditan exactamente los mismos casos, con los mismos fragmentos:
- Las 25 respuestas reales de la evaluación de antes de los candados
  (resultados_agente_sin_candados.json). Se sabe cuáles 2 debieron abstenerse y no lo hicieron.
- 8 respuestas echadas a perder a propósito, donde se sabe que hay un error:
    · 4 con la respuesta de OTRA pregunta (debe reprobarlas por irrelevantes);
    · 4 con un dato inventado agregado al final (debe notar que ya no son fieles).

Guarda todo en evaluacion/comparacion_auditores.json y muestra la tabla.
"""

import asyncio
import json
import sys
from pathlib import Path

CARPETA = Path(__file__).resolve().parent
sys.path.insert(0, str(CARPETA.parent / "api"))

from agente import crear_cliente  # noqa: E402
from auditor import ACTIVO, AuditoriaFallida, Caso, auditar  # noqa: E402
from busqueda import traer_fragmentos  # noqa: E402

# Las respuestas de antes de los candados (sin corrección), para que haya errores que detectar
RESPUESTAS = CARPETA / "resultados_agente_sin_candados.json"

MODELOS = sys.argv[1:3] if len(sys.argv) >= 3 else ["claude-haiku-5-5", "claude-sonnet-5-5"]

# (pregunta que se hace, respuesta que se le pega): respuestas de otra pregunta
CAMBIADAS = [(11, 4), (14, 3), (16, 12), (19, 2)]
# (pregunta, dato falso que se agrega a su respuesta): suenan creíbles pero no están en el artículo
INVENTADAS = [
    (2, "Los autores reportan que seguir estas fases reduce las alucinaciones en un 42 % "
        "frente a GPT-4."),
    (3, "BM25 fue propuesto por Google en 2015 y hoy lo usan todos los buscadores comerciales."),
    (4, "El artículo concluye que esta técnica hace innecesario el reordenamiento (re-ranking)."),
    (10, "Según la Tabla 2, filtrar documentos requiere al menos 64 GPUs para funcionar."),
]


def respuesta_de(fila: dict, texto: str | None = None, citas: list[str] | None = None) -> Caso:
    return Caso(
        pregunta=fila["pregunta"],
        respuesta=texto if texto is not None else fila["respuesta"],
        se_puede_responder=fila["decision_correcta"] == (fila["tipo"] != "no responde"),
        citados=citas if citas is not None else fila["citas"])


def armar_casos(filas: list[dict]) -> list[dict]:
    """Cada caso: la pregunta, la respuesta a auditar y qué debería decir el auditor."""
    por_n = {f["n"]: f for f in filas}
    casos = [{"caso": f"real {f['n']}", "tipo": "real", "pregunta": f["pregunta"],
              "respuesta": respuesta_de(f), "recuperados": f["recuperados"],
              # Lo único que se sabe de las reales: si acertó en responder o abstenerse
              "esperado_abstencion": f["decision_correcta"]} for f in filas]
    for n, otra in CAMBIADAS:
        f, o = por_n[n], por_n[otra]
        casos.append({"caso": f"cambiada {n}←{otra}", "tipo": "cambiada", "pregunta": f["pregunta"],
                      "respuesta": respuesta_de(f, o["respuesta"], citas=[]),
                      "recuperados": f["recuperados"]})
    for n, falso in INVENTADAS:
        f = por_n[n]
        casos.append({"caso": f"inventada {n}", "tipo": "inventada", "pregunta": f["pregunta"],
                      "respuesta": respuesta_de(f, f"{f['respuesta']} {falso}"),
                      "recuperados": f["recuperados"]})
    return casos


def acerto(caso: dict, a: dict) -> bool | None:
    """¿El auditor dijo lo que debía? None si en ese caso no hay respuesta conocida."""
    if caso["tipo"] == "cambiada":
        return a["veredicto"] == "reprobada" or a["relevancia"] <= 2
    if caso["tipo"] == "inventada":
        return a["fidelidad"] != "fiel" and a["veredicto"] != "aprobada"
    return a["abstencion_correcta"] == caso["esperado_abstencion"]


async def correr(modelo: str, casos: list[dict], fragmentos: dict[str, list]) -> list[dict]:
    """Audita todos los casos con un modelo, de 4 en 4 a la vez."""
    limite = asyncio.Semaphore(4)

    async def uno(caso):
        async with limite:
            try:
                a = await auditar(caso["respuesta"], fragmentos[caso["caso"]], modelo=modelo)
            except AuditoriaFallida as error:
                print(f"  {modelo} · {caso['caso']}: no se pudo ({error})", flush=True)
                return None
            return a.model_dump()

    return await asyncio.gather(*(uno(c) for c in casos))


def resumir(modelo: str, casos: list[dict], auditorias: list[dict | None]) -> dict:
    pares = [(c, a) for c, a in zip(casos, auditorias) if a]

    def cuenta(filtro):
        sub = [(c, a) for c, a in pares if filtro(c)]
        return f"{sum(acerto(c, a) for c, a in sub)}/{len(sub)}"

    reales = [a for c, a in pares if c["tipo"] == "real"]
    total = sum(a["costo_usd"] for _, a in pares)
    return {
        "modelo": modelo,
        "detecta respuesta de otra pregunta": cuenta(lambda c: c["tipo"] == "cambiada"),
        "detecta dato inventado": cuenta(lambda c: c["tipo"] == "inventada"),
        "detecta abstención fallida": cuenta(lambda c: c["tipo"] == "real"
                                             and not c["esperado_abstencion"]),
        "no da falsa alarma de abstención": cuenta(lambda c: c["tipo"] == "real"
                                                   and c["esperado_abstencion"]),
        "aciertos totales": cuenta(lambda c: True),
        "reales aprobadas": f"{sum(a['veredicto'] == 'aprobada' for a in reales)}/{len(reales)}",
        "reales fieles": f"{sum(a['fidelidad'] == 'fiel' for a in reales)}/{len(reales)}",
        "costo total (US$)": round(total, 4),
        "costo por auditoría (US$)": round(total / max(len(pares), 1), 5),
        "segundos por auditoría": round(sum(a["segundos"] for _, a in pares)
                                        / max(len(pares), 1), 2),
        "tokens de salida por auditoría": round(sum(a["tokens_salida"] for _, a in pares)
                                                / max(len(pares), 1)),
    }


def main() -> None:
    if not ACTIVO:
        sys.exit("Falta CLAUDE_API_KEY: corre con  uv run --env-file ../.env python …")
    filas = json.loads(RESPUESTAS.read_text(encoding="utf-8"))["preguntas"]
    if any("recuperados" not in f for f in filas):
        sys.exit(f"{RESPUESTAS.name} no trae 'recuperados'")
    casos = armar_casos(filas)
    cliente = crear_cliente()
    fragmentos = {c["caso"]: traer_fragmentos(cliente, c["recuperados"]) for c in casos}

    # Un solo asyncio.run: el cliente de Anthropic no se puede reusar en otro bucle
    async def todos():
        return {m: await correr(m, casos, fragmentos) for m in MODELOS}

    resultados = asyncio.run(todos())
    resumenes = [resumir(m, casos, resultados[m]) for m in MODELOS]

    # Qué tan seguido coinciden los dos en el veredicto de las respuestas reales
    a, b = (resultados[m] for m in MODELOS)
    iguales = [x["veredicto"] == y["veredicto"] for c, x, y in zip(casos, a, b)
               if c["tipo"] == "real" and x and y]

    ancho = max(len(k) for k in resumenes[0]) + 2
    print()
    for clave in resumenes[0]:
        print(f"{clave:<{ancho}}" + "".join(f"{str(r[clave]):<22}" for r in resumenes))
    print(f"{'mismo veredicto en las reales':<{ancho}}{sum(iguales)}/{len(iguales)}")

    print("\nCasos donde no coinciden en el acierto:")
    for c, x, y in zip(casos, a, b):
        if x and y and acerto(c, x) != acerto(c, y):
            print(f"  {c['caso']:<16} {MODELOS[0]}: {x['veredicto']}/{x['fidelidad']}/"
                  f"{x['relevancia']}   {MODELOS[1]}: {y['veredicto']}/{y['fidelidad']}/"
                  f"{y['relevancia']}")

    detalle = [{"caso": c["caso"], "tipo": c["tipo"], "pregunta": c["pregunta"],
                "respuesta": c["respuesta"].respuesta,
                **{m: resultados[m][k] for m in MODELOS},
                **{f"acerto_{m}": (acerto(c, resultados[m][k]) if resultados[m][k] else None)
                   for m in MODELOS}}
               for k, c in enumerate(casos)]
    salida = CARPETA / "comparacion_auditores.json"
    salida.write_text(json.dumps({"resumen": resumenes,
                                  "mismo_veredicto_reales": f"{sum(iguales)}/{len(iguales)}",
                                  "casos": detalle}, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(f"\nGuardado en {salida}")


if __name__ == "__main__":
    main()
