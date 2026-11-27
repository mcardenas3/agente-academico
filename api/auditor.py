"""Auditor de respuestas con Claude (API de Anthropic).

Después de que el agente local responde, Claude revisa la respuesta contra los fragmentos que de
verdad devolvió la búsqueda: si todo lo que afirma está en ellos, si las citas la sostienen, si
responde lo que se preguntó y si debió contestar o decir que el artículo no lo cubre.

Corre en la nube, así que no ocupa RAM ni GPU de la máquina. Se activa solo si hay llave en
.env (ANTHROPIC_API_KEY o CLAUDE_API_KEY); sin llave, el chat funciona igual, sin auditoría.
"""

import os
import time
from typing import Literal

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic
from pydantic import BaseModel, Field

from agente import RespuestaCitada
from busqueda import Fragmento

# Haiku por defecto: cuesta ~US$0.001 por auditoría (Opus 5.5 cuesta ~40 veces más). Se cambia
# con MODELO_AUDITOR, p. ej. claude-sonnet-5-5 o claude-opus-5-5
MODELO_AUDITOR = os.environ.get("MODELO_AUDITOR", "claude-haiku-5-5")
_LLAVE = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY") or ""
ACTIVO = bool(_LLAVE)

# Precio en dólares por millón de tokens (entrada, salida), para llevar la cuenta en /admin.
# Haiku 5.5 cobra este precio con prompts de hasta 100K tokens; aquí son ~4K
PRECIOS = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

INSTRUCCIONES = """\
Eres un auditor de calidad de un asistente que responde preguntas sobre un solo artículo \
científico: "A Survey on Retrieval-Augmented Text Generation for Large Language Models" (Huang y \
Huang, ACM Computing Surveys, 2026).

El asistente busca en el artículo, recibe fragmentos y responde citando algunos de ellos. Si los \
fragmentos no contienen la respuesta, debe abstenerse con un mensaje fijo ("El artículo no trata \
este tema…"). Tu trabajo es revisar una respuesta ya dada, con base SOLO en los fragmentos que \
recibió el asistente; no uses lo que sepas del tema para dar por buena una afirmación.

Las citas se muestran aparte, debajo del texto, como enlaces a la sección y la página: son los \
fragmentos con citado="sí". Que el texto de la respuesta no mencione fuentes NO es un problema.

Revisa:
- fidelidad: cada afirmación de la respuesta debe estar respaldada por los fragmentos. \
"fiel" si todas lo están; "parcialmente_fiel" si alguna afirmación secundaria no aparece; \
"no_fiel" si la idea central no sale de los fragmentos o los contradice. Una abstención es "fiel".
- relevancia (1–5): qué tanto responde lo que se preguntó. 5 = lo responde completo y directo; \
1 = no tiene que ver. Si se abstuvo y los fragmentos de verdad no traían la respuesta, pon 5.
- citas_correctas: los fragmentos citados sostienen la respuesta (no basta con que sean del tema). \
Si se abstuvo, es true cuando no citó nada.
- abstencion_correcta: true si la decisión de contestar o abstenerse fue la correcta según los \
fragmentos recuperados (contestó y había con qué, o se abstuvo y no había).
- veredicto: "aprobada" si es fiel, relevante (4–5) y bien citada; "reprobada" si no es fiel o \
debió hacer lo contrario (contestar o abstenerse); "con_observaciones" en otro caso.

Escribe analisis, problemas y resumen en español, breves y concretos. En problemas pon cada \
falla en una frase (lista vacía si no hay ninguna). El resumen es una sola frase para el usuario.
"""


class Veredicto(BaseModel):
    """Lo que Claude devuelve (salida estructurada: el SDK valida contra este esquema)."""

    analisis: str = Field(description="2 a 4 frases: qué afirma la respuesta y si cada cosa "
                                      "aparece en los fragmentos.")
    fidelidad: Literal["fiel", "parcialmente_fiel", "no_fiel"]
    relevancia: Literal[1, 2, 3, 4, 5]
    citas_correctas: bool
    abstencion_correcta: bool
    problemas: list[str] = Field(description="Cada falla concreta en una frase; vacía si no hay.")
    veredicto: Literal["aprobada", "con_observaciones", "reprobada"]
    resumen: str = Field(description="Una frase para el usuario con el resultado de la auditoría.")


class Auditoria(Veredicto):
    """El veredicto más lo que costó obtenerlo."""

    modelo: str
    tokens_entrada: int
    tokens_salida: int
    costo_usd: float
    segundos: float


class AuditoriaFallida(Exception):
    """No se pudo auditar (sin saldo, llave inválida, sin red, Claude se negó…)."""


_cliente: AsyncAnthropic | None = None


def _cliente_api() -> AsyncAnthropic:
    """Un solo cliente para toda la API (reusa conexiones). Reintenta 2 veces los errores
    pasajeros (429, 5xx, red) y no espera más de 60 s por respuesta."""
    global _cliente
    if _cliente is None:
        _cliente = AsyncAnthropic(api_key=_LLAVE, timeout=60, max_retries=2)
    return _cliente


def costo(modelo: str, tokens_entrada: int, tokens_salida: int) -> float:
    entrada, salida = PRECIOS.get(modelo, (0.0, 0.0))
    return round((tokens_entrada * entrada + tokens_salida * salida) / 1_000_000, 6)


def _fragmento_xml(f: Fragmento, citado: bool) -> str:
    return (f'<fragmento id="{f.id}" seccion="{f.seccion}" paginas="{f.paginas}" '
            f'citado="{"sí" if citado else "no"}">\n{f.texto}\n</fragmento>')


def armar_mensaje(pregunta: str, respuesta: RespuestaCitada, fragmentos: list[Fragmento]) -> str:
    """El caso a auditar: la pregunta, lo que recibió el asistente y lo que contestó."""
    citados = {c.id for c in respuesta.citas}
    partes = [f"<pregunta>\n{pregunta}\n</pregunta>"]
    if respuesta.consulta and respuesta.consulta != pregunta:
        # Pregunta de seguimiento: el asistente la reescribió completa para buscar
        partes.append(f"<pregunta_reescrita>\n{respuesta.consulta}\n</pregunta_reescrita>")
    if fragmentos:
        partes.append("<fragmentos_recuperados>\n"
                      + "\n".join(_fragmento_xml(f, f.id in citados) for f in fragmentos)
                      + "\n</fragmentos_recuperados>")
    else:
        partes.append("<fragmentos_recuperados>(el asistente no buscó)</fragmentos_recuperados>")
    estado = "contestó" if respuesta.se_puede_responder else "se abstuvo"
    partes.append(f"<respuesta_del_asistente decision=\"{estado}\">\n{respuesta.respuesta}\n"
                  "</respuesta_del_asistente>")
    return "\n\n".join(partes)


async def auditar(pregunta: str, respuesta: RespuestaCitada, fragmentos: list[Fragmento]
                  ) -> Auditoria:
    """Pide a Claude el veredicto de una respuesta. Lanza AuditoriaFallida si no se pudo."""
    if not ACTIVO:
        raise AuditoriaFallida("Falta la llave de la API de Claude en .env")
    inicio = time.perf_counter()
    try:
        r = await _cliente_api().messages.parse(
            model=MODELO_AUDITOR,
            # Incluye lo que Claude piensa antes de contestar; el veredicto en sí son ~300 tokens
            max_tokens=8000,
            # Esfuerzo explícito: "medium" basta para comparar una respuesta con 5 fragmentos
            output_config={"effort": "medium"},
            system=INSTRUCCIONES,
            messages=[{"role": "user", "content": armar_mensaje(pregunta, respuesta, fragmentos)}],
            output_format=Veredicto,
        )
    except APIStatusError as error:  # 400 sin saldo, 401 llave, 429 límite, 5xx…
        raise AuditoriaFallida(f"{type(error).__name__} ({error.status_code}): "
                               f"{error.message}") from error
    except APIConnectionError as error:  # sin red o se agotó el tiempo
        raise AuditoriaFallida(f"Sin conexión con la API de Claude: {error}") from error

    if r.stop_reason == "refusal":
        raise AuditoriaFallida("Claude se negó a auditar esta respuesta")
    if r.stop_reason == "max_tokens" or r.parsed_output is None:
        raise AuditoriaFallida(f"La auditoría no terminó (stop_reason={r.stop_reason})")
    return Auditoria(
        **r.parsed_output.model_dump(),
        modelo=MODELO_AUDITOR,
        tokens_entrada=r.usage.input_tokens,
        tokens_salida=r.usage.output_tokens,
        costo_usd=costo(MODELO_AUDITOR, r.usage.input_tokens, r.usage.output_tokens),
        segundos=round(time.perf_counter() - inicio, 2),
    )
