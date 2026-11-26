"""Comprueba que busqueda.py da los mismos fragmentos que MM30f en la evaluación de la Etapa 4.

Uso (desde la carpeta api, con opensearch y vllm encendidos):
    uv run python comparar_busqueda.py

Para las 25 preguntas de ingesta/evaluar_busqueda.py en sus tres condiciones (en, es, es→en),
corre las dos búsquedas con el MISMO vector y compara los 5 fragmentos que devuelven. Si
coinciden en todo, la tool del agente implementa exactamente lo que se midió.
"""

import json
import sys
import time
from pathlib import Path

from opensearchpy import OpenSearch

from busqueda import K, URL_OPENSEARCH, buscar_con_vector, preparar_pipeline

# La evaluación vive en el otro proyecto de Python (ingesta/). Se agrega su carpeta a las rutas
# donde Python busca módulos para reutilizar sus preguntas y su MM30f sin copiarlos. Solo importa
# httpx y opensearch-py, que también están instalados en api/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingesta"))
from evaluar_busqueda import (  # noqa: E402  (el import va después de ajustar sys.path)
    CONDICIONES, METODOS, PREFIJO_ID, PREGUNTAS, TRADUCCIONES,
    buscar, lugar_del_relevante, recall_5, vectorizar_preguntas,
)


def main() -> None:
    traducciones = json.loads(TRADUCCIONES.read_text(encoding="utf-8"))
    cliente = OpenSearch(hosts=[URL_OPENSEARCH])
    preparar_pipeline(cliente)

    # Los tres textos de cada pregunta, igual que en la evaluación
    textos = {
        "inglés": [p[0] for p in PREGUNTAS],
        "español": [p[1] for p in PREGUNTAS],
        "traducción": [traducciones[p[1]] for p in PREGUNTAS],
    }
    n = len(PREGUNTAS)
    # Un solo juego de vectores para las dos búsquedas: así una diferencia solo puede venir de
    # cómo se arma la consulta, no de la pequeña variación de vLLM entre llamadas
    todos = vectorizar_preguntas(textos["inglés"] + textos["español"])
    vectores = {"en": todos[:n], "es": todos[n:]}

    print(f"{'Cond.':<7}{'Iguales':>9}{'MRR':>8}{'Rec@5':>8}{'ms por búsqueda':>17}")
    diferentes = []
    for condicion, (texto_bm25, idioma_vector) in CONDICIONES.items():
        iguales, lugares, recalls, tiempos = 0, [], [], []
        for k, (en, es, relevantes, _) in enumerate(PREGUNTAS):
            texto, vector = textos[texto_bm25][k], vectores[idioma_vector][k]

            # La de la evaluación: sus 10 resultados, de los que se comparan los 5 primeros
            esperados = buscar(cliente, METODOS["MM30f"](texto, vector))[:K]

            # La nueva: se mide cuánto tarda (sin la vectorización, que va aparte)
            inicio = time.perf_counter()
            fragmentos = buscar_con_vector(cliente, texto, vector)
            tiempos.append((time.perf_counter() - inicio) * 1000)
            obtenidos = [int(f.id.removeprefix(PREFIJO_ID)) for f in fragmentos]

            if obtenidos == esperados:
                iguales += 1
            else:
                diferentes.append((condicion, k + 1, esperados, obtenidos))
            lugar = lugar_del_relevante(obtenidos, relevantes)
            lugares.append(1 / lugar if lugar else 0)
            recalls.append(recall_5(obtenidos, relevantes))

        print(f"{condicion:<7}{f'{iguales}/{n}':>9}{sum(lugares) / n:>8.3f}"
              f"{sum(recalls) / n:>8.3f}{sum(tiempos) / n:>17.1f}")

    # Si algo no coincide, se muestra qué pregunta y qué fragmentos salieron en cada una
    for condicion, numero, esperados, obtenidos in diferentes:
        print(f"Diferente: {condicion} #{numero}  evaluación {esperados}  busqueda.py {obtenidos}")

    # Un ejemplo de lo que recibirá el agente
    print("\nEjemplo (es→en #9):", textos["traducción"][8])
    for f in buscar_con_vector(cliente, textos["traducción"][8], vectores["es"][8]):
        print(f"  {f.id}  p.{f.paginas}  {f.seccion}  ({len(f.texto)} caracteres)")


if __name__ == "__main__":
    main()
