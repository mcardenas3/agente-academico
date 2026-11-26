"""El agente que responde preguntas sobre el artículo, construido con Pydantic AI.

- El LLM es nemotron-3-nano:4b servido por Ollama (por su API compatible con OpenAI).
- Tiene dos tools: la búsqueda híbrida y las fichas de la bibliografía por número.
- Devuelve una respuesta tipada: el texto, si el artículo permite responder y qué fragmentos usó.
  Las secciones y páginas de las citas no las escribe el LLM: se sacan de los fragmentos que de
  verdad devolvió la búsqueda, para que no pueda inventarlas.
"""

import os
import time
from dataclasses import dataclass, field

# Pydantic AI imprime un anuncio de su servicio de monitoreo al correr un agente; esto lo apaga
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from opensearchpy import OpenSearch  # noqa: E402  (los imports van después de la línea de arriba)
from pydantic import BaseModel, Field  # noqa: E402
from pydantic_ai import Agent, ModelSettings, RunContext, ToolOutput  # noqa: E402
from pydantic_ai.models.ollama import OllamaModel  # noqa: E402
from pydantic_ai.providers.ollama import OllamaProvider  # noqa: E402

from busqueda import (  # noqa: E402
    URL_OPENSEARCH, Fragmento, ResultadoReferencias, buscar_fragmentos, preparar_pipeline,
    traer_referencias,
)

# Ollama por su API compatible con OpenAI: la URL termina en /v1. 127.0.0.1 y no "localhost"
# (en Windows, Python esperaría el timeout en ::1)
URL_OLLAMA = "http://127.0.0.1:11434/v1"
# Siempre con ":4b": "latest" apunta al de 30b, que no cabe en la GPU
MODELO_LLM = "nemotron-3-nano:4b"

# Instrucciones del agente. Van en inglés porque el modelo es chico y sigue mejor instrucciones en
# inglés; la respuesta sí sale en el idioma de la pregunta (regla 5). La frase de la terminología
# viene de la Etapa 4: sin ella, "recuperación" se traducía "recovery" y BM25 no encontraba nada
INSTRUCCIONES = """\
You answer questions about one scientific article: "A Survey on Retrieval-Augmented Text \
Generation for Large Language Models" (Huang and Huang, ACM Computing Surveys, 2026).

Rules:
1. ALWAYS call buscar_en_articulo before answering, even if you think you know the answer. Pass \
the user's question as `consulta` and its English translation as `consulta_en`. The questions are \
about information retrieval and retrieval-augmented generation (RAG), so use the standard English \
terminology of that field.
2. Answer ONLY with information from the fragments returned by the tools. Do not add facts from \
your own knowledge.
3. If a fragment cites a study by number, like "REALM [48]", and the question is about that \
study, call traer_referencias_bibliograficas with those numbers to get its full reference.
4. If the fragments do not contain the answer, set se_puede_responder to false and say briefly \
that the article does not cover it.
5. Write the answer in the same language as the user's question. Be concise.
6. In fragmentos_citados, list the ids of the fragments your answer is based on.
7. Give your final answer ONLY by calling the tool `responder`. Never write the answer as plain \
text.
"""


@dataclass
class Dependencias:
    """Lo que las tools necesitan durante una pregunta. Pydantic AI se lo pasa en ctx.deps."""

    cliente: OpenSearch
    # Fragmentos que devolvió la búsqueda en esta pregunta, por id: de aquí salen las citas
    fragmentos_vistos: dict[str, Fragmento] = field(default_factory=dict)


class Respuesta(BaseModel):
    """Final answer to the user's question."""

    respuesta: str = Field(description="The answer, in the language of the user's question.")
    se_puede_responder: bool = Field(
        description="False if the article fragments do not contain the answer.")
    fragmentos_citados: list[str] = Field(
        description="Ids of the fragments the answer is based on, e.g. huang2026-rag-survey-034.")


class Cita(BaseModel):
    """De dónde sale la respuesta: un fragmento con su sección y sus páginas."""

    id: str
    seccion: str
    paginas: list[int]


class RespuestaCitada(BaseModel):
    """Lo que devuelve preguntar(): la respuesta, sus citas y lo que costó obtenerla."""

    respuesta: str
    se_puede_responder: bool
    citas: list[Cita]
    ids_no_vistos: list[str]   # ids que el LLM citó pero que la búsqueda nunca le dio
    tools_llamadas: list[str]  # en orden, para revisar que sí buscó
    llamadas_llm: int          # cuántas veces se le habló al LLM (cada tool agrega una vuelta)
    tokens_entrada: int
    tokens_salida: int
    segundos: float


modelo = OllamaModel(MODELO_LLM, provider=OllamaProvider(base_url=URL_OLLAMA))

agente = Agent(
    modelo,
    deps_type=Dependencias,
    # La respuesta final llega como llamada a una tool llamada "responder" con los campos de
    # Respuesta. Medido: con el nombre por defecto ("final_result") el modelo escribía la
    # respuesta como texto y fallaba 3 de 6 preguntas; con "responder" y la regla 7, 12 de 12.
    # Los otros dos modos (NativeOutput y PromptedOutput) fallaron peor: obligan a contestar en
    # JSON desde la primera vuelta, así que el modelo nunca llamaba la búsqueda e inventaba
    output_type=ToolOutput(Respuesta, name="responder",
                           description="Give the final answer to the user. Call this after searching."),
    instructions=INSTRUCCIONES,
    model_settings=ModelSettings(
        # Temperatura 0: siempre la opción más probable, para que repita sus decisiones
        temperature=0,
        # Sin pensar: el modelo lo trae encendido y solo agrega tiempo. thinking=False de Pydantic
        # AI no llega a Ollama con este modelo (no está en su lista de familias conocidas), así
        # que se manda directo el campo que Ollama sí entiende en su API compatible con OpenAI
        extra_body={"reasoning_effort": "none"},
    ),
    # Si el LLM manda argumentos mal formados, se le devuelve el error y puede intentar otra vez
    retries=2,
)


@agente.tool
def buscar_en_articulo(ctx: RunContext[Dependencias], consulta: str, consulta_en: str
                       ) -> list[Fragmento]:
    """Search the article and return the 5 most relevant fragments.

    Args:
        consulta: The user's question exactly as written, in its original language.
        consulta_en: The same question translated into English.
    """
    fragmentos = buscar_fragmentos(ctx.deps.cliente, consulta, consulta_en)
    # Se guardan para convertir después los ids citados en sección y páginas
    for f in fragmentos:
        ctx.deps.fragmentos_vistos[f.id] = f
    return fragmentos


@agente.tool
def traer_referencias_bibliograficas(ctx: RunContext[Dependencias], numeros: list[int]
                                     ) -> ResultadoReferencias:
    """Get the full bibliography entries for citation numbers like [48] (up to 10 at once).

    Args:
        numeros: The citation numbers, e.g. [48] or [48, 82].
    """
    return traer_referencias(ctx.deps.cliente, numeros)


def crear_cliente() -> OpenSearch:
    """Cliente de OpenSearch con el pipeline de la búsqueda híbrida ya guardado."""
    cliente = OpenSearch(hosts=[URL_OPENSEARCH])
    preparar_pipeline(cliente)
    return cliente


def preguntar(cliente: OpenSearch, pregunta: str) -> RespuestaCitada:
    """Hace una pregunta al agente y devuelve la respuesta con sus citas."""
    dependencias = Dependencias(cliente=cliente)
    inicio = time.perf_counter()
    resultado = agente.run_sync(pregunta, deps=dependencias)
    segundos = time.perf_counter() - inicio

    salida = resultado.output  # ya es un objeto Respuesta, revisado por Pydantic
    vistos = dependencias.fragmentos_vistos
    # Las tools que pidió el LLM, en orden, leídas del historial de mensajes de esta pregunta
    tools = [parte.tool_name
             for mensaje in resultado.new_messages() for parte in mensaje.parts
             if parte.part_kind == "tool-call" and parte.tool_name != "responder"]
    uso = resultado.usage  # tokens y llamadas al LLM de toda la pregunta (todas las vueltas)
    return RespuestaCitada(
        respuesta=salida.respuesta,
        se_puede_responder=salida.se_puede_responder,
        citas=[Cita(id=i, seccion=vistos[i].seccion, paginas=vistos[i].paginas)
               for i in dict.fromkeys(salida.fragmentos_citados) if i in vistos],
        ids_no_vistos=[i for i in salida.fragmentos_citados if i not in vistos],
        tools_llamadas=tools,
        llamadas_llm=uso.requests,
        tokens_entrada=uso.input_tokens,
        tokens_salida=uso.output_tokens,
        segundos=round(segundos, 1),
    )
