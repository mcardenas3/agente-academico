"""El agente que responde preguntas sobre el artículo, construido con Pydantic AI.

- El LLM es nemotron-3-nano:4b servido por Ollama (por su API compatible con OpenAI).
- Tiene dos tools: la búsqueda híbrida y las fichas de la bibliografía por número.
- Devuelve una respuesta tipada: el texto, si el artículo permite responder y qué fragmentos usó.
  Las secciones y páginas de las citas no las escribe el LLM: se sacan de los fragmentos que de
  verdad devolvió la búsqueda, para que no pueda inventarlas.
- Recibe los últimos turnos de la conversación, así entiende preguntas de seguimiento.
- Candados antes de entregar la respuesta (en revisar_respuesta): tiene que haber buscado, solo
  puede citar fragmentos que la búsqueda le dio, si contesta tiene que citar al menos uno, y
  Claude verifica que todo lo que afirma salga de los fragmentos; si no, el agente la corrige.
"""

import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

# Pydantic AI imprime un anuncio de su servicio de monitoreo al correr un agente; esto lo apaga
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from opensearchpy import OpenSearch  # noqa: E402  (los imports van después de la línea de arriba)
from pydantic import BaseModel, Field  # noqa: E402
from pydantic_ai import Agent, ModelRetry, ModelSettings, RunContext, ToolOutput  # noqa: E402
from pydantic_ai.exceptions import UnexpectedModelBehavior  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart,
)
from pydantic_ai.models.ollama import OllamaModel  # noqa: E402
from pydantic_ai.providers.ollama import OllamaProvider  # noqa: E402

from auditor import Auditoria, AuditoriaFallida, Caso, auditar, tiene_problemas  # noqa: E402
from busqueda import (  # noqa: E402
    URL_OPENSEARCH, Fragmento, Referencia, ResultadoReferencias, buscar_fragmentos,
    preparar_pipeline, traer_referencias,
)

# Ollama por su API compatible con OpenAI: la URL termina en /v1. 127.0.0.1 y no "localhost"
# (en Windows, Python esperaría el timeout en ::1). Dentro de Docker se cambia con la variable
URL_OLLAMA = os.environ.get("URL_OLLAMA", "http://127.0.0.1:11434/v1")
# Siempre con ":4b": "latest" apunta al de 30b, que no cabe en la GPU
MODELO_LLM = os.environ.get("MODELO_LLM", "nemotron-3-nano:4b")
# Cuántos turnos anteriores (pregunta + respuesta) recibe el agente. Pocos a propósito: el
# contexto es de 8192 tokens y los 5 fragmentos de cada búsqueda ya ocupan ~3,500
TURNOS_DE_MEMORIA = 3

# Instrucciones del agente. Van en inglés porque el modelo es chico y sigue mejor instrucciones en
# inglés; la respuesta sí sale en el idioma de la pregunta (regla 6). La frase de la terminología
# viene de la Etapa 4: sin ella, "recuperación" se traducía "recovery"
INSTRUCCIONES = """\
You answer questions about one scientific article: "A Survey on Retrieval-Augmented Text \
Generation for Large Language Models" (Huang and Huang, ACM Computing Surveys, 2026).

Rules:
1. ALWAYS call buscar_en_articulo before answering, even if you think you know the answer. Pass \
the user's question as `consulta` and its English translation as `consulta_en`. The questions are \
about information retrieval and retrieval-augmented generation (RAG), so use the standard English \
terminology of that field.
2. If the question refers to earlier messages of the conversation (for example "and how does it \
compare?"), rewrite it as a self-contained question in both `consulta` and `consulta_en`.
3. Answer ONLY with information from the fragments returned by the tools. Do not add facts from \
your own knowledge.
4. Tables list studies with a citation number, like "REALM [48]". If the search result includes \
referencias_mencionadas, use the title of that reference to say what the study is. For other \
citation numbers, call traer_referencias_bibliograficas.
5. If the fragments do not contain the answer, set se_puede_responder to false.
6. Write `respuesta` in the same language as the user's question. Be concise.
7. In fragmentos_citados, list the ids of the fragments your answer is based on.
8. Give your final answer ONLY by calling the tool `responder`. Never write the answer as plain \
text.
"""

# Lo que se responde cuando el artículo no cubre la pregunta. Es fijo a propósito: con su propio
# texto, el modelo a veces contestaba de memoria ("La capital de Francia es París") aunque él
# mismo marcaba que el artículo no lo cubre
NO_CUBRE = {
    "es": "El artículo no trata este tema, así que no puedo responderlo con base en él.",
    "en": "The article does not cover this topic, so I can't answer it based on the article.",
}

# Palabras frecuentes de cada idioma para adivinar en cuál viene la pregunta
_PALABRAS_ES = {"que", "qué", "como", "cómo", "cual", "cuál", "cuales", "cuáles", "el", "la", "los",
                "las", "del", "por", "para", "un", "una", "es", "son", "se", "con", "y", "hay"}
_PALABRAS_EN = {"what", "how", "the", "is", "are", "of", "which", "why", "does", "do", "for", "and",
                "to", "can", "between", "it"}


# Nota que se agrega al final de la pregunta. Medido: con solo la regla 6, las preguntas en inglés
# salían en español (2 de 2); con la nota, en inglés (2 de 2). Va en la pregunta y no en las
# instrucciones porque con cualquier instrucción dinámica el modelo dejaba de buscar (0 de 9)
NOTA_DE_IDIOMA = {"es": "(Responde en español.)", "en": "(Answer in English.)"}


def con_nota_de_idioma(pregunta: str) -> str:
    """La pregunta tal cual, más la nota del idioma en que debe responder."""
    return f"{pregunta}\n\n{NOTA_DE_IDIOMA[idioma_de(pregunta)]}"


def idioma_de(texto: str) -> str:
    """Adivina si un texto está en español ("es") o en inglés ("en")."""
    texto = texto.lower()
    if re.search(r"[¿¡ñáéíóú]", texto):  # signos y letras que el inglés no usa
        return "es"
    palabras = set(re.findall(r"[a-záéíóúñ]+", texto))
    return "es" if len(palabras & _PALABRAS_ES) > len(palabras & _PALABRAS_EN) else "en"


@dataclass
class Dependencias:
    """Lo que las tools necesitan durante una pregunta. Pydantic AI se lo pasa en ctx.deps."""

    cliente: OpenSearch
    # La pregunta del usuario tal cual (sin la nota de idioma), para la verificación
    pregunta: str = ""
    # Fragmentos que devolvió la búsqueda en esta pregunta, por id: de aquí salen las citas
    fragmentos_vistos: dict[str, Fragmento] = field(default_factory=dict)
    # La primera consulta con que buscó (reescrita completa si era de seguimiento)
    consulta: str | None = None
    # Tiempo dentro de las tools (vectorizar + buscar), para separar búsqueda de LLM en /admin
    segundos_busqueda: float = 0.0
    # Verificación con Claude antes de entregar la respuesta
    verificar: bool = False
    auditoria: Auditoria | None = None     # la de la respuesta que se entregó
    correcciones: int = 0                  # veces que Claude la regresó para corregirla
    error_verificacion: str | None = None  # si Claude no respondió (sin red, sin saldo…)


class Respuesta(BaseModel):
    """Final answer to the user's question."""

    respuesta: str = Field(description="The answer, in the language of the user's question.")
    se_puede_responder: bool = Field(
        description="False if the article fragments do not contain the answer.")
    fragmentos_citados: list[str] = Field(
        description="Ids of the fragments the answer is based on, e.g. huang2026-rag-survey-034.")


class ResultadoBusqueda(BaseModel):
    """Lo que devuelve la tool de búsqueda: los fragmentos y, si la pregunta nombra un estudio
    que un fragmento cita por número ("REALM [48]"), la ficha de ese estudio."""

    fragmentos: list[Fragmento]
    referencias_mencionadas: list[Referencia]


class Cita(BaseModel):
    """De dónde sale la respuesta: un fragmento con su sección y sus páginas."""

    id: str
    seccion: str
    paginas: list[int]


class Turno(BaseModel):
    """Una pregunta anterior de la conversación con su respuesta: la memoria del agente."""

    pregunta: str
    respuesta: str


class RespuestaCitada(BaseModel):
    """Lo que devuelve preguntar(): la respuesta, sus citas y lo que costó obtenerla."""

    respuesta: str
    se_puede_responder: bool
    citas: list[Cita]
    consulta: str | None        # la pregunta como la buscó el agente (reescrita si era seguimiento)
    consulta_en: str | None     # su traducción al inglés para BM25
    tools_llamadas: list[str]   # en orden, para revisar que sí buscó
    llamadas_llm: int           # cuántas veces se le habló al LLM (cada tool agrega una vuelta)
    tokens_entrada: int
    tokens_salida: int
    segundos: float             # tiempo total de la pregunta
    segundos_busqueda: float    # de ellos, dentro de las tools (vectorizar + OpenSearch)
    error: str | None = None    # si el modelo no logró entregar una respuesta válida
    # Todos los fragmentos que devolvió la búsqueda (citados o no): el auditor los revisa para
    # saber si había con qué contestar
    recuperados: list[str] = []
    # Verificación con Claude: "verificada", "fallida" (Claude no respondió) u "omitida" (sin
    # llave o sobre el tope diario)
    verificacion: str = "omitida"
    auditoria: Auditoria | None = None
    correcciones: int = 0


modelo = OllamaModel(MODELO_LLM, provider=OllamaProvider(base_url=URL_OLLAMA))

agente = Agent(
    modelo,
    deps_type=Dependencias,
    # La respuesta final llega como llamada a una tool llamada "responder" con los campos de
    # Respuesta. Medido: con el nombre por defecto ("final_result") el modelo escribía la
    # respuesta como texto y fallaba 3 de 6 preguntas; con "responder" y la regla 8, 6 de 6.
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
    # Si el LLM se equivoca (argumentos mal formados, no buscó, citó un id ajeno, no citó nada o
    # Claude encontró algo sin respaldo), se le devuelve el error y puede intentar otra vez, hasta
    # 3 veces
    retries=3,
)


# Nota: se probó agregar una instrucción calculada en código con el idioma de la pregunta
# (@agente.instructions). Con cualquier instrucción dinámica el modelo dejó de llamar la búsqueda
# (0 de 9 preguntas, contra 3 de 3 sin ella); el idioma va en la pregunta (NOTA_DE_IDIOMA)


# Un nombre seguido de su número de cita, como en las tablas: "REALM [48]", "Self-RAG [7]"
_NOMBRE_CON_CITA = re.compile(r"([A-Za-z][\w\-\.]{2,})\s*\[(\d+)\]")
# Cuántas fichas se agregan solas a una búsqueda
MAX_REFERENCIAS_MENCIONADAS = 3


def citas_mencionadas(fragmentos: list[Fragmento], consulta_en: str) -> list[int]:
    """Números de cita de los estudios que la pregunta nombra: si la pregunta dice "REALM" y un
    fragmento dice "REALM [48]", devuelve [48]. Así la ficha llega sin depender de que el modelo
    la pida (medido: con la regla escrita, nunca pedía traer_referencias_bibliograficas)."""
    palabras = set(re.findall(r"[\w\-\.]+", consulta_en.lower()))
    numeros: list[int] = []
    for f in fragmentos:
        for nombre, numero in _NOMBRE_CON_CITA.findall(f.texto):
            if nombre.lower() in palabras and int(numero) not in numeros:
                numeros.append(int(numero))
    return numeros[:MAX_REFERENCIAS_MENCIONADAS]


@agente.tool
def buscar_en_articulo(ctx: RunContext[Dependencias], consulta: str, consulta_en: str
                       ) -> ResultadoBusqueda:
    """Search the article and return the 5 most relevant fragments, plus the full reference of
    any cited study that the question names.

    Args:
        consulta: The user's question in its original language (self-contained).
        consulta_en: The same question translated into English.
    """
    inicio = time.perf_counter()
    fragmentos = buscar_fragmentos(ctx.deps.cliente, consulta, consulta_en)
    numeros = citas_mencionadas(fragmentos, consulta_en)
    referencias = traer_referencias(ctx.deps.cliente, numeros).referencias if numeros else []
    ctx.deps.segundos_busqueda += time.perf_counter() - inicio
    ctx.deps.consulta = ctx.deps.consulta or consulta
    # Se guardan para convertir después los ids citados en sección y páginas
    for f in fragmentos:
        ctx.deps.fragmentos_vistos[f.id] = f
    return ResultadoBusqueda(fragmentos=fragmentos, referencias_mencionadas=referencias)


@agente.tool
def traer_referencias_bibliograficas(ctx: RunContext[Dependencias], numeros: list[int]
                                     ) -> ResultadoReferencias:
    """Get the full bibliography entries for citation numbers like [48] (up to 10 at once).

    Args:
        numeros: The citation numbers, e.g. [48] or [48, 82].
    """
    try:
        return traer_referencias(ctx.deps.cliente, numeros)
    except ValueError as error:
        # Pedido inválido (vacío o demasiados): se le explica al modelo para que lo corrija
        raise ModelRetry(str(error)) from error


# Respuestas que en realidad dicen que el artículo no trata el tema ("El artículo no proporciona
# los resultados de GPT-7…"). Medido: así fallaron las 2 abstenciones de la evaluación; el
# modelo lo decía en el texto pero marcaba que sí se podía responder
_NIEGA_EL_TEMA = re.compile(
    r"\b(el art[ií]culo|los autores|el texto|el documento|los fragmentos|the article|the authors"
    r"|the paper|the text|the fragments)\b[^.]{0,40}?\b(no|not|n't)\b[^.]{0,25}?"
    r"\b(menciona|proporciona|trata|aborda|incluye|presenta|expresa|expresan|contiene|discute"
    r"|reporta|ofrece|habla|especifica|da|mention|provide|discuss|cover|include|address|report"
    r"|express|contain|specify|present)", re.IGNORECASE)
# Solo cuenta si la respuesta es corta: "El artículo no menciona X, pero explica Y…" sí responde
LARGO_MAXIMO_NEGACION = 250
# Cuántas veces puede Claude regresar una respuesta para que se corrija
MAX_CORRECCIONES = 1


def niega_el_tema(texto: str) -> bool:
    """¿La respuesta solo dice que el artículo no trata lo que se preguntó?"""
    return len(texto) <= LARGO_MAXIMO_NEGACION and bool(_NIEGA_EL_TEMA.search(texto))


def _abstencion() -> Respuesta:
    return Respuesta(respuesta="", se_puede_responder=False, fragmentos_citados=[])


@agente.output_validator
async def revisar_respuesta(ctx: RunContext[Dependencias], salida: Respuesta) -> Respuesta:
    """Los candados antes de entregar una respuesta. Si algo falla, se le explica al modelo y lo
    intenta otra vez (ModelRetry); así no puede contestar con algo que no salga del artículo.

    1. Tiene que haber buscado.
    2. Solo puede citar fragmentos que la búsqueda de verdad le dio.
    3. Si dice que sí se puede responder, tiene que citar al menos un fragmento.
    4. Si su respuesta solo dice que el artículo no trata el tema, se toma como abstención.
    5. Tiene que responder en el idioma de la pregunta.
    6. Claude revisa la respuesta contra los fragmentos. Si afirma algo sin respaldo, cita mal o
       debió abstenerse, se le regresan los problemas para que la corrija (una vez).
    """
    d = ctx.deps
    vistos = d.fragmentos_vistos
    if not vistos:
        raise ModelRetry("You must call buscar_en_articulo before answering.")
    ajenos = [i for i in salida.fragmentos_citados if i not in vistos]
    if ajenos:
        raise ModelRetry(f"These ids were not returned by the search: {ajenos}. "
                         f"Cite only ids from this list: {list(vistos)}.")
    texto = _IDS_EN_TEXTO.sub("", salida.respuesta).strip()
    if salida.se_puede_responder and niega_el_tema(texto):
        salida = _abstencion()
    if salida.se_puede_responder and not salida.fragmentos_citados:
        if ctx.last_attempt:  # sin citas no hay respuesta: mejor abstenerse que inventar
            salida = _abstencion()
        else:
            raise ModelRetry(
                "Your answer must be based on the fragments: list in fragmentos_citados the ids "
                "of the fragments that support it. If the fragments do not contain the answer, "
                "set se_puede_responder to false.")
    idioma = idioma_de(d.pregunta)
    if salida.se_puede_responder and idioma_de(texto) != idioma and not ctx.last_attempt:
        # Medido: tras una corrección (los problemas vienen en español), una respuesta a una
        # pregunta en inglés salió en español
        raise ModelRetry("Write `respuesta` in the same language as the user's question. "
                         f"{NOTA_DE_IDIOMA[idioma]}")

    if not d.verificar:
        return salida
    caso = Caso(pregunta=d.pregunta, consulta=d.consulta,
                respuesta=texto if salida.se_puede_responder else NO_CUBRE[idioma_de(d.pregunta)],
                se_puede_responder=salida.se_puede_responder,
                citados=list(dict.fromkeys(salida.fragmentos_citados)))
    try:
        a = await auditar(caso, list(vistos.values()))
    except AuditoriaFallida as error:
        # Claude no respondió: se entrega sin verificar (la interfaz lo dice) en vez de no responder
        d.error_verificacion = str(error)
        return salida
    # Solo se corrige lo que afirma algo; una abstención no tiene afirmaciones que corregir
    if (salida.se_puede_responder and tiene_problemas(a)
            and d.correcciones < MAX_CORRECCIONES and not ctx.last_attempt):
        d.correcciones += 1
        problemas = "\n".join(f"- {p}" for p in a.problemas) or f"- {a.resumen}"
        raise ModelRetry(
            "An auditor checked your answer against the article fragments and found these "
            f"problems:\n{problemas}\n\nRewrite the answer using ONLY information that appears in "
            "the fragments, and list in fragmentos_citados the fragments that support it. If the "
            "fragments do not contain the answer, set se_puede_responder to false. Keep the "
            f"language of the user's question. {NOTA_DE_IDIOMA[idioma]}")
    d.auditoria = a
    return salida


def crear_cliente() -> OpenSearch:
    """Cliente de OpenSearch con el pipeline de la búsqueda híbrida ya guardado."""
    cliente = OpenSearch(hosts=[URL_OPENSEARCH])
    preparar_pipeline(cliente)
    return cliente


def historial_para_el_agente(turnos: list[Turno]) -> list[ModelMessage]:
    """Convierte los últimos turnos en mensajes de Pydantic AI.

    Cada respuesta anterior va como lo que de verdad fue: una llamada a la tool `responder`.
    Medido: con las respuestas como texto simple, el modelo imitaba ese formato en la pregunta
    de seguimiento, contestaba en texto sin buscar y se agotaban los reintentos.
    Solo van las preguntas y las respuestas, no los fragmentos que se buscaron: cada búsqueda
    ocupa ~3,500 tokens y con tres turnos se llenaría el contexto. Si el seguimiento necesita el
    artículo, el agente vuelve a buscar.
    """
    mensajes: list[ModelMessage] = []
    for k, turno in enumerate(turnos[-TURNOS_DE_MEMORIA:]):
        id_llamada = f"turno-{k}"
        mensajes.append(ModelRequest(parts=[UserPromptPart(content=turno.pregunta)]))
        mensajes.append(ModelResponse(parts=[ToolCallPart(
            tool_name="responder", tool_call_id=id_llamada,
            args={"respuesta": turno.respuesta, "se_puede_responder": True,
                  "fragmentos_citados": []})]))
        mensajes.append(ModelRequest(parts=[ToolReturnPart(
            tool_name="responder", tool_call_id=id_llamada, content="Final result processed.")]))
    return mensajes


# Ids de fragmentos que el modelo a veces copia dentro del texto: "[huang2026-rag-survey-013, …]"
_IDS_EN_TEXTO = re.compile(r"\s*\[?huang2026-rag-survey-\d+(?:,\s*huang2026-rag-survey-\d+)*\]?")


def _armar_respuesta(pregunta: str, salida: Respuesta | None, mensajes: list[ModelMessage],
                     uso, dependencias: Dependencias, segundos: float,
                     error: str | None = None) -> RespuestaCitada:
    """Junta la salida del modelo, las citas reales y las métricas en un RespuestaCitada."""
    vistos = dependencias.fragmentos_vistos
    llamadas = [p for m in mensajes for p in m.parts if p.part_kind == "tool-call"]
    tools = [p.tool_name for p in llamadas if p.tool_name != "responder"]
    # Lo que el agente buscó: los argumentos de su primera búsqueda
    busqueda = next((p.args_as_dict() for p in llamadas if p.tool_name == "buscar_en_articulo"), {})

    if salida is None or not salida.se_puede_responder:
        # Sin respuesta válida o fuera del artículo: mensaje fijo y sin citas
        texto, se_puede, citas = NO_CUBRE[idioma_de(pregunta)], False, []
    else:
        # Las citas se muestran aparte; los ids dentro del texto sobran
        texto, se_puede = _IDS_EN_TEXTO.sub("", salida.respuesta).strip(), True
        citas = [Cita(id=i, seccion=vistos[i].seccion, paginas=vistos[i].paginas)
                 for i in dict.fromkeys(salida.fragmentos_citados) if i in vistos]
    return RespuestaCitada(
        respuesta=texto, se_puede_responder=se_puede, citas=citas,
        consulta=busqueda.get("consulta"), consulta_en=busqueda.get("consulta_en"),
        tools_llamadas=tools,
        llamadas_llm=uso.requests if uso else 0,
        tokens_entrada=uso.input_tokens if uso else 0,
        tokens_salida=uso.output_tokens if uso else 0,
        segundos=round(segundos, 2),
        segundos_busqueda=round(dependencias.segundos_busqueda, 2),
        error=error,
        recuperados=list(vistos),
        verificacion=("verificada" if dependencias.auditoria else
                      "fallida" if dependencias.verificar else "omitida"),
        auditoria=dependencias.auditoria,
        correcciones=dependencias.correcciones,
    )


def preguntar(cliente: OpenSearch, pregunta: str, turnos: list[Turno] | None = None,
              verificar: bool = False) -> RespuestaCitada:
    """Hace una pregunta al agente (con los turnos anteriores) y devuelve la respuesta citada.
    Con verificar=True, Claude la revisa antes de entregarla."""
    dependencias = Dependencias(cliente=cliente, pregunta=pregunta, verificar=verificar)
    inicio = time.perf_counter()
    try:
        resultado = agente.run_sync(con_nota_de_idioma(pregunta), deps=dependencias,
                                    message_history=historial_para_el_agente(turnos or []))
    except UnexpectedModelBehavior as error:
        # Se agotaron los reintentos: se responde con el mensaje fijo y se guarda el motivo
        return _armar_respuesta(pregunta, None, [], None, dependencias,
                                time.perf_counter() - inicio, error=str(error))
    return _armar_respuesta(pregunta, resultado.output, resultado.new_messages(),
                            resultado.usage, dependencias, time.perf_counter() - inicio)


# --- Versión con eventos, para que la API los mande al navegador mientras ocurren ---

@dataclass
class Evento:
    """Un aviso de lo que va pasando: "buscando", "fragmentos", "texto" (pedazo) o "final"."""

    tipo: str
    datos: dict


async def preguntar_por_eventos(cliente: OpenSearch, pregunta: str,
                                turnos: list[Turno] | None = None,
                                verificar: bool = False) -> AsyncIterator[Evento]:
    """Igual que preguntar(), pero va avisando: cuándo busca, cuándo Claude verifica y, al final,
    la respuesta completa. La API los convierte en eventos SSE para la interfaz."""
    dependencias = Dependencias(cliente=cliente, pregunta=pregunta, verificar=verificar)
    inicio = time.perf_counter()
    salida, mensajes, uso, error = None, [], None, None
    intentos = 0  # respuestas que el modelo ha propuesto (la 2.ª es una corrección)
    try:
        async with agente.iter(con_nota_de_idioma(pregunta), deps=dependencias,
                               message_history=historial_para_el_agente(turnos or [])) as corrida:
            async for nodo in corrida:
                # Cada vuelta con tools: se avisa qué pidió el modelo. El nodo llega antes de
                # ejecutarse, así que "verificando" sale justo antes de que Claude revise
                if Agent.is_call_tools_node(nodo):
                    for parte in nodo.model_response.parts:
                        if parte.part_kind != "tool-call":
                            continue
                        if parte.tool_name != "responder":
                            yield Evento("buscando", {"tool": parte.tool_name,
                                                      "argumentos": parte.args_as_dict()})
                        elif verificar:
                            intentos += 1
                            # correcciones > 0: esta respuesta corrige lo que Claude regresó
                            yield Evento("verificando", {
                                "intento": intentos, "correcciones": dependencias.correcciones})
            salida = corrida.result.output
            mensajes = corrida.result.new_messages()
            uso = corrida.result.usage
    except UnexpectedModelBehavior as e:
        error = str(e)
    respuesta = _armar_respuesta(pregunta, salida, mensajes, uso, dependencias,
                                 time.perf_counter() - inicio, error=error)
    yield Evento("final", respuesta.model_dump())
