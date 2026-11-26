"""La API del agente académico (FastAPI).

Rutas:
    POST /api/chat                     pregunta → eventos SSE ("conversacion", "buscando",
                                       "respuesta" o "error")
    GET  /api/conversaciones?cliente_id=…  historial de un navegador (sin login)
    GET  /api/conversaciones/{id}      mensajes de una conversación, para volver a pintarla
    DELETE /api/conversaciones/{id}?cliente_id=…  la quita del historial (no borra datos)
    GET  /api/fragmentos?ids=…         texto exacto de los fragmentos citados (hasta 10)
    GET  /articulo.pdf                 el artículo; las citas lo abren en su página (#page=N)
    GET  /api/salud                    estado de cada servicio
    GET  /api/admin/resumen            totales y promedios          ┐
    GET  /api/admin/por-dia            serie diaria para la gráfica ├ piden "Authorization: Bearer <ADMIN_TOKEN>"
    GET  /api/admin/preguntas          pregunta, respuesta y costo  ┘
    /                                  la interfaz de React ya compilada (web/dist)

Correr en desarrollo (desde api/, con los servicios encendidos):
    uv run uvicorn main:app --port 8080
"""

import asyncio
import logging
import os
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

import base_datos as bd
import cache
from agente import (
    MODELO_LLM, URL_OLLAMA, RespuestaCitada, Turno, TURNOS_DE_MEMORIA, crear_cliente,
    preguntar_por_eventos,
)
from busqueda import INDICE, URL_EMBED, URL_OPENSEARCH, Fragmento, traer_fragmentos, vectorizar

# Forma de los ids de fragmento del índice
ID_FRAGMENTO = re.compile(r"huang2026-rag-survey-\d{3}")

registro = logging.getLogger("agente")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Token del panel /admin; viene de .env. Sin él, el panel queda cerrado
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
# La interfaz compilada; en Docker se copia aquí al construir la imagen
CARPETA_WEB = Path(os.environ.get("CARPETA_WEB", Path(__file__).resolve().parent.parent / "web" / "dist"))
# El artículo en PDF, para que las citas abran la página exacta (/articulo.pdf#page=16)
ARCHIVO_PDF = Path(os.environ.get(
    "ARCHIVO_PDF", Path(__file__).resolve().parent.parent / "datos" / "huang2026-rag-survey.pdf"))


async def calentar() -> None:
    """Primera llamada a vLLM y a Ollama al arrancar, para que el primer usuario no pague el
    arranque en frío (medido: la primera pregunta tardaba 30–110 s y las siguientes ~5 s)."""
    inicio = time.perf_counter()
    try:
        await asyncio.to_thread(vectorizar, "warm up")
        async with httpx.AsyncClient(timeout=300) as http:
            await http.post(URL_OLLAMA.removesuffix("/v1") + "/api/generate", json={
                "model": MODELO_LLM, "prompt": "hi", "stream": False, "think": False,
                "keep_alive": -1, "options": {"num_predict": 1}})
        registro.info("Modelos listos en %.1f s", time.perf_counter() - inicio)
    except Exception as error:  # no detiene la API: solo la primera pregunta será lenta
        registro.warning("No se pudo calentar los modelos: %s", error)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    """Al arrancar: conexiones, tablas, pipeline de búsqueda y calentamiento. Al apagar: cierra."""
    app.state.grupo = bd.crear_grupo()
    await app.state.grupo.open(wait=True)
    await bd.aplicar_esquema(app.state.grupo)
    app.state.redis = cache.crear_cliente()
    app.state.opensearch = crear_cliente()
    tarea = asyncio.create_task(calentar())
    yield
    tarea.cancel()
    await app.state.redis.aclose()
    await app.state.grupo.close()


app = FastAPI(title="Agente académico", lifespan=ciclo_de_vida)


# --- Chat ---

class Pregunta(BaseModel):
    pregunta: str = Field(min_length=1, max_length=1000)
    conversacion_id: uuid.UUID | None = None  # sin id, empieza una conversación nueva
    cliente_id: uuid.UUID | None = None       # id aleatorio del navegador, para su historial


async def turnos_anteriores(request: Request, conversacion_id: uuid.UUID) -> list[Turno]:
    """Historial reciente: primero de Redis; si no está, de PostgreSQL (y se guarda en Redis)."""
    turnos = await cache.leer_historial(request.app.state.redis, conversacion_id)
    if turnos is None:
        turnos = await bd.ultimos_turnos(request.app.state.grupo, conversacion_id,
                                         TURNOS_DE_MEMORIA)
        await cache.guardar_historial(request.app.state.redis, conversacion_id, turnos)
    return turnos


@app.post("/api/chat", response_class=EventSourceResponse)
async def chat(entrada: Pregunta, request: Request) -> AsyncIterable[ServerSentEvent]:
    """Responde una pregunta y va avisando por eventos (Server-Sent Events) lo que pasa."""
    estado = request.app.state
    pregunta = entrada.pregunta.strip()
    conversacion_id = entrada.conversacion_id or uuid.uuid4()
    try:
        await bd.asegurar_conversacion(estado.grupo, conversacion_id, entrada.cliente_id, pregunta)
        yield ServerSentEvent(event="conversacion", data={"id": str(conversacion_id)})
        turnos = await turnos_anteriores(request, conversacion_id)

        # 1. ¿Ya se respondió esta misma pregunta? Solo si abre la conversación (sin historial)
        respuesta, desde_cache, inicio = None, False, time.perf_counter()
        if not turnos:
            guardada = await cache.leer_respuesta(estado.redis, pregunta)
            if guardada:
                # Se registra lo que costó de verdad esta vez: sin LLM y casi sin tiempo
                respuesta = guardada.model_copy(update={
                    "segundos": round(time.perf_counter() - inicio, 3), "segundos_busqueda": 0.0,
                    "tokens_entrada": 0, "tokens_salida": 0, "llamadas_llm": 0})
                desde_cache = True

        # 2. Si no, el agente: se reenvían sus avisos de búsqueda conforme ocurren
        if respuesta is None:
            async for evento in preguntar_por_eventos(estado.opensearch, pregunta, turnos):
                if evento.tipo == "buscando":
                    yield ServerSentEvent(event="buscando", data=evento.datos)
                elif evento.tipo == "final":
                    respuesta = RespuestaCitada(**evento.datos)
            if not turnos:
                await cache.guardar_respuesta(estado.redis, pregunta, respuesta)

        # 3. Se guarda todo y se manda la respuesta
        mensaje_id = await bd.guardar_turno(estado.grupo, conversacion_id, pregunta, respuesta,
                                            desde_cache)
        await cache.guardar_historial(estado.redis, conversacion_id,
                                      turnos + [Turno(pregunta=pregunta,
                                                      respuesta=respuesta.respuesta)])
        yield ServerSentEvent(event="respuesta", data={
            **respuesta.model_dump(), "desde_cache": desde_cache, "mensaje_id": mensaje_id})
    except Exception as error:  # un servicio caído: se le avisa a la interfaz en vez de colgarse
        registro.exception("Falló /api/chat")
        yield ServerSentEvent(event="error", data={
            "mensaje": "No se pudo responder: un servicio no está disponible. "
                       "Intenta de nuevo en un momento.", "detalle": type(error).__name__})


@app.get("/api/conversaciones")
async def historial(request: Request, cliente_id: uuid.UUID,
                    limite: int = Query(50, ge=1, le=200)) -> list[dict]:
    """Las conversaciones de un navegador, la más reciente primero (la barra lateral)."""
    return await bd.conversaciones_de(request.app.state.grupo, cliente_id, limite)


@app.get("/api/conversaciones/{conversacion_id}")
async def conversacion(conversacion_id: uuid.UUID, request: Request) -> list[dict]:
    return await bd.mensajes_de(request.app.state.grupo, conversacion_id)


@app.delete("/api/conversaciones/{conversacion_id}", status_code=204)
async def ocultar(conversacion_id: uuid.UUID, cliente_id: uuid.UUID, request: Request) -> None:
    """Quita una conversación del historial de su navegador (no borra sus datos)."""
    if not await bd.ocultar_conversacion(request.app.state.grupo, conversacion_id, cliente_id):
        raise HTTPException(404, "No existe esa conversación en este navegador")


@app.get("/api/fragmentos")
async def fragmentos(request: Request, ids: list[str] = Query(max_length=10)) -> list[Fragmento]:
    """El texto exacto de los fragmentos citados, para mostrarlo debajo de la respuesta."""
    # Solo ids con la forma de los del índice, p. ej. huang2026-rag-survey-034
    validos = [i for i in ids if ID_FRAGMENTO.fullmatch(i)]
    return await asyncio.to_thread(traer_fragmentos, request.app.state.opensearch, validos)


@app.get("/articulo.pdf", include_in_schema=False)
async def articulo() -> FileResponse:
    """El PDF del artículo (CC BY 4.0). "inline" hace que el navegador lo abra en su visor, que
    entiende #page=N en la URL; así cada cita lleva a la página de donde salió."""
    return FileResponse(ARCHIVO_PDF, media_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="huang2026-rag-survey.pdf"'})


@app.get("/api/salud")
async def salud(request: Request) -> dict:
    """Revisa cada servicio; sirve para el healthcheck de Docker y para diagnosticar."""
    estado = request.app.state
    revisiones = {}

    async def revisar(nombre, corrutina):
        try:
            await asyncio.wait_for(corrutina, timeout=5)
            revisiones[nombre] = "ok"
        except Exception as error:
            revisiones[nombre] = f"error: {type(error).__name__}"

    async def postgres():
        async with estado.grupo.connection() as conexion:
            await conexion.execute("SELECT 1")

    async with httpx.AsyncClient() as http:
        await asyncio.gather(
            revisar("postgres", postgres()),
            revisar("redis", estado.redis.ping()),
            revisar("opensearch", http.get(f"{URL_OPENSEARCH}/{INDICE}/_count")),
            revisar("vllm", http.get(URL_EMBED.replace("/v2/embed", "/health"))),
            revisar("ollama", http.get(URL_OLLAMA.removesuffix("/v1") + "/api/version")),
        )
    return {"ok": all(v == "ok" for v in revisiones.values()), "servicios": revisiones}


# --- Panel /admin ---

def verificar_admin(authorization: str = Header(default="")) -> None:
    """Deja pasar solo con "Authorization: Bearer <ADMIN_TOKEN>". compare_digest compara en
    tiempo constante, para no dar pistas del token por cuánto tarda en rechazar."""
    if not ADMIN_TOKEN:
        raise HTTPException(503, "El panel está desactivado: falta ADMIN_TOKEN en .env")
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(401, "Token incorrecto")


@app.get("/api/admin/resumen", dependencies=[Depends(verificar_admin)])
async def admin_resumen(request: Request, desde: datetime | None = None,
                        hasta: datetime | None = None) -> dict:
    return await bd.resumen(request.app.state.grupo, desde, hasta)


@app.get("/api/admin/por-dia", dependencies=[Depends(verificar_admin)])
async def admin_por_dia(request: Request, desde: datetime | None = None,
                        hasta: datetime | None = None) -> list[dict]:
    return await bd.por_dia(request.app.state.grupo, desde, hasta)


@app.get("/api/admin/preguntas", dependencies=[Depends(verificar_admin)])
async def admin_preguntas(request: Request, desde: datetime | None = None,
                          hasta: datetime | None = None, texto: str | None = None,
                          limite: int = Query(50, ge=1, le=200),
                          desplazamiento: int = Query(0, ge=0)) -> list[dict]:
    return await bd.lista_de_preguntas(request.app.state.grupo, desde, hasta, texto, limite,
                                       desplazamiento)


# --- La interfaz: al final, para que las rutas /api/* tengan prioridad ---
if CARPETA_WEB.is_dir():
    app.frontend("/", directory=CARPETA_WEB, fallback="index.html")
