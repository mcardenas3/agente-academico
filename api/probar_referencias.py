"""Prueba de traer_referencias(): la tool que trae fichas de la bibliografía por su número.

Uso (desde la carpeta api, con opensearch y vllm encendidos):
    uv run python probar_referencias.py

Revisa casos sueltos (una ficha, varias, repetidas, números que no existen, pedidos inválidos),
que las 193 fichas se encuentren con su número, y el caso que la motivó: "¿Qué es REALM?".
"""

import re

from opensearchpy import OpenSearch

from busqueda import (
    MAX_REFERENCIAS, URL_OPENSEARCH, buscar_fragmentos, preparar_pipeline, traer_referencias,
)

# Cuántas fichas tiene la bibliografía del artículo
TOTAL_REFERENCIAS = 193


def mostrar(titulo: str, numeros: list[int], cliente: OpenSearch) -> None:
    """Pide esos números y muestra el principio de cada ficha y los que no existen."""
    resultado = traer_referencias(cliente, numeros)
    print(f"\n{titulo}: pedidos {numeros}")
    for r in resultado.referencias:
        print(f"  [{r.numero}] p.{r.paginas}  {r.texto[:90]}")
    print(f"  no encontradas: {resultado.no_encontradas}")


def main() -> None:
    cliente = OpenSearch(hosts=[URL_OPENSEARCH])

    # 1. Casos sueltos
    mostrar("Una ficha", [48], cliente)
    mostrar("Varias, con una repetida (debe quedar una vez y en el orden pedido)", [82, 48, 82],
            cliente)
    mostrar("Números que no existen", [0, 194, 48], cliente)

    # 2. Pedidos inválidos: deben dar un error con mensaje claro, no tronar de otra forma
    print("\nPedidos inválidos:")
    for numeros in ([], list(range(1, MAX_REFERENCIAS + 2))):
        try:
            traer_referencias(cliente, numeros)
            print(f"  {len(numeros)} números: ¡no dio error!")
        except ValueError as error:
            print(f"  {len(numeros)} números: {error}")

    # 3. Las 193 fichas, de 10 en 10: cada una debe aparecer y su texto debe empezar con su número
    encontradas, mal_numeradas = 0, []
    for inicio in range(1, TOTAL_REFERENCIAS + 1, MAX_REFERENCIAS):
        lote = list(range(inicio, min(inicio + MAX_REFERENCIAS, TOTAL_REFERENCIAS + 1)))
        resultado = traer_referencias(cliente, lote)
        encontradas += len(resultado.referencias)
        mal_numeradas += [r.numero for r in resultado.referencias
                          if not r.texto.startswith(f"[{r.numero}]")]
    print(f"\nBibliografía completa: {encontradas}/{TOTAL_REFERENCIAS} encontradas; "
          f"con número que no coincide con su texto: {mal_numeradas or 'ninguna'}")

    # 4. El caso que la motivó: la búsqueda encuentra "REALM [48]" en el texto y la ficha se trae
    # por el número, porque la [48] no contiene la palabra "REALM"
    preparar_pipeline(cliente)
    fragmentos = buscar_fragmentos(cliente, "¿Qué es REALM?", "What is REALM?")
    print("\n¿Qué es REALM? — fragmentos de la búsqueda:")
    for f in fragmentos:
        print(f"  {f.id}  p.{f.paginas}  {f.seccion}")
    # Números que aparecen junto a "REALM" en los fragmentos: "REALM [48]" → 48
    citas = sorted({int(n) for f in fragmentos for n in re.findall(r"REALM\s*\[(\d+)\]", f.texto)})
    print(f"  citas junto a REALM: {citas}")
    if citas:
        mostrar("  Ficha de REALM", citas, cliente)


if __name__ == "__main__":
    main()
