"""Experimento: ¿cómo pedirle al modelo la respuesta tipada?

Uso (desde la carpeta api, con opensearch, vllm y ollama encendidos; tarda ~3 minutos):
    uv run python probar_modos_salida.py

Pydantic AI tiene tres formas de obtener una salida estructurada:
- ToolOutput (la de por defecto): la respuesta llega como llamada a una tool especial;
- NativeOutput: Ollama obliga al modelo a escribir JSON que cumpla el esquema;
- PromptedOutput: el esquema va en las instrucciones y se lee el JSON del texto.
Corre las 6 preguntas de probar_agente.py con cada forma (y con ToolOutput renombrada a
"responder") y cuenta cuántas terminan bien, cuántas llamaron la búsqueda (hayan terminado o no) y
cuántas citan algún fragmento que la búsqueda nunca devolvió (una cita inventada).
"""

import time

from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput

from agente import (
    INSTRUCCIONES, Dependencias, Respuesta, agente, buscar_en_articulo, crear_cliente, modelo,
    traer_referencias_bibliograficas,
)
from probar_agente import PREGUNTAS

# La regla 7 de INSTRUCCIONES nombra la tool "responder"; para las otras formas se quita, para
# no pedirle al modelo una tool que no existe
SIN_REGLA_7 = INSTRUCCIONES.split("7. ")[0]

# Nombre → (forma de salida, instrucciones)
CONFIGURACIONES = {
    "Tool (final_result)": (ToolOutput(Respuesta), SIN_REGLA_7),
    "Native": (NativeOutput(Respuesta), SIN_REGLA_7),
    "Prompted": (PromptedOutput(Respuesta), SIN_REGLA_7),
    "Tool (responder)": (ToolOutput(Respuesta, name="responder",
                                    description="Give the final answer to the user. "
                                                "Call this after searching."),
                         INSTRUCCIONES),
}


def main() -> None:
    cliente = crear_cliente()
    print(f"{'Configuración':<22}{'Terminan':>10}{'Buscaron':>10}{'Citas inventadas':>18}"
          f"{'s prom.':>9}")
    for nombre, (salida, instrucciones) in CONFIGURACIONES.items():
        # Mismo modelo, ajustes y tools que el agente; solo cambia la forma de salida
        prueba = Agent(modelo, deps_type=Dependencias, output_type=salida,
                       instructions=instrucciones, model_settings=agente.model_settings,
                       retries=2, tools=[buscar_en_articulo, traer_referencias_bibliograficas])
        terminan = buscaron = inventadas = 0
        tiempos = []
        for pregunta, _ in PREGUNTAS:
            dependencias = Dependencias(cliente=cliente)
            inicio = time.perf_counter()
            try:
                resultado = prueba.run_sync(pregunta, deps=dependencias)
            except Exception:  # se agotaron los reintentos: no entregó la respuesta tipada
                resultado = None
            tiempos.append(time.perf_counter() - inicio)
            # fragmentos_vistos solo se llena si la tool de búsqueda corrió, aunque luego fallara
            buscaron += bool(dependencias.fragmentos_vistos)
            if resultado is None:
                continue
            terminan += 1
            # No citar nada es válido (p. ej. si no se puede responder); citar un id ajeno, no
            citas = resultado.output.fragmentos_citados
            inventadas += any(i not in dependencias.fragmentos_vistos for i in citas)
        n = len(PREGUNTAS)
        print(f"{nombre:<22}{f'{terminan}/{n}':>10}{f'{buscaron}/{n}':>10}"
              f"{f'{inventadas}/{terminan}':>18}{sum(tiempos) / n:>9.1f}")


if __name__ == "__main__":
    main()
