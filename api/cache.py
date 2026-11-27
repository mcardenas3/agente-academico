"""Lo que la API guarda en Redis: respuestas ya calculadas e historial reciente de cada chat.

1. Caché de respuestas. Una pregunta que abre una conversación (sin historial) siempre recibe la
   misma respuesta (temperatura 0), así que se guarda. Las preguntas de seguimiento NO se cachean:
   su respuesta depende de lo que se habló antes.
2. Historial reciente. Los últimos turnos de cada conversación, para no ir a PostgreSQL en cada
   pregunta. Si no están (expiraron o Redis se reinició), se leen de PostgreSQL.
3. Auditorías. Cada respuesta en caché lleva dentro su auditoría (RespuestaCitada.auditoria): si
   la respuesta se repite, no se le vuelve a pagar a Claude. Y un contador por día pone tope a lo
   que se gasta.
"""

import hashlib
import json
import os
import re
import uuid
from datetime import date

from redis.asyncio import Redis

from agente import MODELO_LLM, TURNOS_DE_MEMORIA, RespuestaCitada, Turno

URL_REDIS = os.environ.get("URL_REDIS", "redis://127.0.0.1:6379/0")
# Cuánto dura una respuesta en caché: un día. Si se vuelve a indexar el artículo o cambia el
# agente, basta esperar o borrar las llaves "respuesta:*"
DURACION_RESPUESTA = 24 * 3600
# Cuánto dura el historial de una conversación sin actividad
DURACION_HISTORIAL = 24 * 3600


def crear_cliente() -> Redis:
    return Redis.from_url(URL_REDIS, decode_responses=True)


def _normalizar(pregunta: str) -> str:
    """Misma pregunta = misma llave aunque cambien mayúsculas, espacios o signos al final."""
    texto = re.sub(r"\s+", " ", pregunta.strip().lower())
    return texto.rstrip("?¿!. ").lstrip("¿¡ ")


def llave_respuesta(pregunta: str) -> str:
    """La llave incluye el modelo: si cambia el LLM, las respuestas viejas no se reusan."""
    resumen = hashlib.sha256(f"{MODELO_LLM}|{_normalizar(pregunta)}".encode()).hexdigest()[:32]
    return f"respuesta:{resumen}"


async def leer_respuesta(redis: Redis, pregunta: str) -> RespuestaCitada | None:
    guardada = await redis.get(llave_respuesta(pregunta))
    return RespuestaCitada.model_validate_json(guardada) if guardada else None


async def guardar_respuesta(redis: Redis, pregunta: str, respuesta: RespuestaCitada) -> None:
    # Solo respuestas sanas: un error no se cachea, para que la siguiente vez se intente otra vez
    if respuesta.error is None:
        await redis.set(llave_respuesta(pregunta), respuesta.model_dump_json(),
                        ex=DURACION_RESPUESTA)


async def contar_auditoria_del_dia(redis: Redis) -> int:
    """Suma una auditoría al contador de hoy y devuelve cuántas van (para el tope diario)."""
    llave = f"auditorias:{date.today().isoformat()}"
    cuantas = await redis.incr(llave)
    if cuantas == 1:
        await redis.expire(llave, 2 * 24 * 3600)
    return cuantas


def _llave_historial(conversacion_id: uuid.UUID) -> str:
    return f"conversacion:{conversacion_id}:turnos"


async def leer_historial(redis: Redis, conversacion_id: uuid.UUID) -> list[Turno] | None:
    """None = Redis no lo tiene (hay que ir a PostgreSQL); [] = la conversación está vacía."""
    guardado = await redis.get(_llave_historial(conversacion_id))
    if guardado is None:
        return None
    return [Turno(**t) for t in json.loads(guardado)]


async def guardar_historial(redis: Redis, conversacion_id: uuid.UUID, turnos: list[Turno]) -> None:
    ultimos = [t.model_dump() for t in turnos[-TURNOS_DE_MEMORIA:]]
    await redis.set(_llave_historial(conversacion_id), json.dumps(ultimos, ensure_ascii=False),
                    ex=DURACION_HISTORIAL)
