"""Todo lo que la API guarda y lee de PostgreSQL: conversaciones, mensajes, métricas y citas."""

import os
import uuid
from datetime import datetime
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from agente import MODELO_LLM, RespuestaCitada, Turno
from auditor import Auditoria

# postgresql://usuario:clave@servidor:puerto/base. Dentro de Docker el servidor es "postgres"
URL_BASE_DATOS = os.environ.get(
    "URL_BASE_DATOS", "postgresql://agente:agente@127.0.0.1:5432/agente_academico")
ESQUEMA = Path(__file__).resolve().parent / "esquema.sql"


def crear_grupo() -> AsyncConnectionPool:
    """Grupo de conexiones: se abren unas cuantas y se reusan en cada petición."""
    return AsyncConnectionPool(URL_BASE_DATOS, min_size=1, max_size=5, open=False,
                               kwargs={"row_factory": dict_row})


async def aplicar_esquema(grupo: AsyncConnectionPool) -> None:
    """Crea las tablas si no existen (esquema.sql)."""
    async with grupo.connection() as conexion:
        await conexion.execute(ESQUEMA.read_text(encoding="utf-8"))


# Largo máximo del título que se ve en el historial
LARGO_TITULO = 80


async def asegurar_conversacion(grupo: AsyncConnectionPool, conversacion_id: uuid.UUID,
                                cliente_id: uuid.UUID | None, pregunta: str) -> None:
    """Crea la conversación (con la pregunta como título) o la marca como recién usada.

    Si una conversación vieja no tenía dueño, la toma el navegador que la siguió: así la
    conversación que ya estaba abierta antes de existir el historial también aparece en él."""
    titulo = pregunta if len(pregunta) <= LARGO_TITULO else pregunta[:LARGO_TITULO - 1] + "…"
    async with grupo.connection() as conexion:
        await conexion.execute(
            "INSERT INTO conversaciones (id, cliente_id, titulo) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET actualizada_en = now(), "
            " cliente_id = COALESCE(conversaciones.cliente_id, EXCLUDED.cliente_id), "
            # Una conversación de antes del historial no tiene título: se usa su primera pregunta
            " titulo = COALESCE(conversaciones.titulo, (SELECT left(contenido, %s) FROM mensajes"
            "  WHERE conversacion_id = EXCLUDED.id AND rol = 'usuario' ORDER BY id LIMIT 1),"
            "  EXCLUDED.titulo)",
            (conversacion_id, cliente_id, titulo, LARGO_TITULO))


async def conversaciones_de(grupo: AsyncConnectionPool, cliente_id: uuid.UUID, limite: int
                            ) -> list[dict]:
    """El historial de un navegador: sus conversaciones visibles, la más reciente primero."""
    async with grupo.connection() as conexion:
        return await (await conexion.execute(
            "SELECT c.id, c.titulo, c.actualizada_en,"
            " (SELECT count(*) FROM mensajes m WHERE m.conversacion_id = c.id"
            "  AND m.rol = 'usuario') AS preguntas"
            " FROM conversaciones c WHERE c.cliente_id = %s AND NOT c.oculta"
            " ORDER BY c.actualizada_en DESC LIMIT %s", (cliente_id, limite))).fetchall()


async def ocultar_conversacion(grupo: AsyncConnectionPool, conversacion_id: uuid.UUID,
                               cliente_id: uuid.UUID) -> bool:
    """La quita del historial (solo si es de ese navegador). No borra nada: /admin la sigue
    contando. Devuelve False si no existe o es de otro navegador."""
    async with grupo.connection() as conexion:
        resultado = await conexion.execute(
            "UPDATE conversaciones SET oculta = true WHERE id = %s AND cliente_id = %s",
            (conversacion_id, cliente_id))
        return resultado.rowcount > 0


async def guardar_turno(grupo: AsyncConnectionPool, conversacion_id: uuid.UUID, pregunta: str,
                        respuesta: RespuestaCitada, desde_cache: bool) -> int:
    """Guarda la pregunta, la respuesta, sus métricas y sus citas en una sola transacción."""
    async with grupo.connection() as conexion, conexion.transaction():
        await conexion.execute(
            "INSERT INTO mensajes (conversacion_id, rol, contenido) VALUES (%s, 'usuario', %s)",
            (conversacion_id, pregunta))
        fila = await (await conexion.execute(
            "INSERT INTO mensajes (conversacion_id, rol, contenido) VALUES (%s, 'agente', %s) "
            "RETURNING id", (conversacion_id, respuesta.respuesta))).fetchone()
        mensaje_id = fila["id"]
        await conexion.execute(
            "INSERT INTO metricas (mensaje_id, modelo, tokens_entrada, tokens_salida, llamadas_llm,"
            " segundos_total, segundos_busqueda, se_puede_responder, desde_cache, consulta_en,"
            " tools, error) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (mensaje_id, MODELO_LLM, respuesta.tokens_entrada, respuesta.tokens_salida,
             respuesta.llamadas_llm, respuesta.segundos, respuesta.segundos_busqueda,
             respuesta.se_puede_responder, desde_cache, respuesta.consulta_en,
             respuesta.tools_llamadas, respuesta.error))
        for cita in respuesta.citas:
            await conexion.execute(
                "INSERT INTO citas (mensaje_id, fragmento_id, seccion, paginas) "
                "VALUES (%s, %s, %s, %s)", (mensaje_id, cita.id, cita.seccion, cita.paginas))
    return mensaje_id


async def guardar_auditoria(grupo: AsyncConnectionPool, mensaje_id: int, auditoria: Auditoria,
                            desde_cache: bool) -> None:
    """Guarda el veredicto de Claude sobre una respuesta del agente."""
    a = auditoria
    async with grupo.connection() as conexion:
        await conexion.execute(
            "INSERT INTO auditorias (mensaje_id, modelo, veredicto, fidelidad, relevancia,"
            " citas_correctas, abstencion_correcta, problemas, resumen, analisis, tokens_entrada,"
            " tokens_salida, costo_usd, segundos, desde_cache)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (mensaje_id) DO NOTHING",
            (mensaje_id, a.modelo, a.veredicto, a.fidelidad, a.relevancia, a.citas_correctas,
             a.abstencion_correcta, a.problemas, a.resumen, a.analisis, a.tokens_entrada,
             a.tokens_salida, a.costo_usd, a.segundos, desde_cache))


# La auditoría de un mensaje como objeto JSON (NULL si no se auditó), para pegarla a cada fila
_AUDITORIA_JSON = """(
    SELECT json_build_object('veredicto', a.veredicto, 'fidelidad', a.fidelidad,
                             'relevancia', a.relevancia, 'citas_correctas', a.citas_correctas,
                             'abstencion_correcta', a.abstencion_correcta,
                             'problemas', a.problemas, 'resumen', a.resumen,
                             'analisis', a.analisis, 'modelo', a.modelo,
                             'costo_usd', a.costo_usd::float, 'desde_cache', a.desde_cache)
      FROM auditorias a WHERE a.mensaje_id = m.id) AS auditoria"""


async def ultimos_turnos(grupo: AsyncConnectionPool, conversacion_id: uuid.UUID, cuantos: int
                         ) -> list[Turno]:
    """Los últimos turnos (pregunta + respuesta) de una conversación, del más viejo al más nuevo.

    Cada respuesta va justo después de su pregunta (mismo turno, ids consecutivos), así que se
    emparejan por orden."""
    async with grupo.connection() as conexion:
        filas = await (await conexion.execute(
            "SELECT rol, contenido FROM mensajes WHERE conversacion_id = %s "
            "ORDER BY id DESC LIMIT %s", (conversacion_id, cuantos * 2))).fetchall()
    filas.reverse()
    turnos = []
    for usuario, agente in zip(filas[::2], filas[1::2]):
        if usuario["rol"] == "usuario" and agente["rol"] == "agente":
            turnos.append(Turno(pregunta=usuario["contenido"], respuesta=agente["contenido"]))
    return turnos


async def mensajes_de(grupo: AsyncConnectionPool, conversacion_id: uuid.UUID) -> list[dict]:
    """Todos los mensajes de una conversación con sus citas y su auditoría, para volver a
    pintarla."""
    async with grupo.connection() as conexion:
        return await (await conexion.execute(
            "SELECT m.id, m.rol, m.contenido, m.creado_en,"
            " COALESCE(json_agg(json_build_object('id', c.fragmento_id, 'seccion', c.seccion,"
            "  'paginas', c.paginas) ORDER BY c.id) FILTER (WHERE c.id IS NOT NULL), '[]') AS citas,"
            f" {_AUDITORIA_JSON}"
            " FROM mensajes m LEFT JOIN citas c ON c.mensaje_id = m.id"
            " WHERE m.conversacion_id = %s GROUP BY m.id ORDER BY m.id",
            (conversacion_id,))).fetchall()


# --- Lo que lee el panel /admin ---

def _filtro_fechas(desde: datetime | None, hasta: datetime | None) -> tuple[str, list]:
    """Parte del WHERE para limitar por fecha de la respuesta (m = mensajes del agente)."""
    condiciones, valores = [], []
    if desde:
        condiciones.append("m.creado_en >= %s")
        valores.append(desde)
    if hasta:
        condiciones.append("m.creado_en < %s")
        valores.append(hasta)
    return (" AND " + " AND ".join(condiciones)) if condiciones else "", valores


async def resumen(grupo: AsyncConnectionPool, desde: datetime | None, hasta: datetime | None
                  ) -> dict:
    """Totales y promedios para las tarjetas del panel."""
    filtro, valores = _filtro_fechas(desde, hasta)
    consulta = f"""
        SELECT count(*)                                              AS preguntas,
               count(DISTINCT m.conversacion_id)                     AS conversaciones,
               coalesce(sum(x.tokens_entrada), 0)                    AS tokens_entrada,
               coalesce(sum(x.tokens_salida), 0)                     AS tokens_salida,
               (avg(x.tokens_entrada) FILTER (WHERE NOT x.desde_cache))::float AS tokens_entrada_prom,
               (avg(x.tokens_salida)  FILTER (WHERE NOT x.desde_cache))::float AS tokens_salida_prom,
               avg(x.segundos_total)                                  AS segundos_prom,
               avg(x.segundos_total) FILTER (WHERE NOT x.desde_cache) AS segundos_prom_sin_cache,
               avg(x.segundos_total) FILTER (WHERE x.desde_cache)     AS segundos_prom_cache,
               avg(x.segundos_busqueda) FILTER (WHERE NOT x.desde_cache) AS segundos_busqueda_prom,
               avg(x.segundos_total - x.segundos_busqueda) FILTER (WHERE NOT x.desde_cache)
                                                                      AS segundos_llm_prom,
               count(*) FILTER (WHERE x.desde_cache)                  AS desde_cache,
               count(*) FILTER (WHERE NOT x.se_puede_responder)       AS sin_respuesta,
               count(*) FILTER (WHERE x.error IS NOT NULL)            AS errores
          FROM mensajes m JOIN metricas x ON x.mensaje_id = m.id
         WHERE m.rol = 'agente' {filtro}"""
    # Lo que dijo el auditor (Claude) en el mismo periodo
    consulta_auditorias = f"""
        SELECT count(*)                                              AS auditadas,
               count(*) FILTER (WHERE a.veredicto = 'aprobada')          AS aprobadas,
               count(*) FILTER (WHERE a.veredicto = 'con_observaciones') AS con_observaciones,
               count(*) FILTER (WHERE a.veredicto = 'reprobada')         AS reprobadas,
               count(*) FILTER (WHERE a.fidelidad = 'fiel')              AS fieles,
               count(*) FILTER (WHERE a.citas_correctas)                 AS citas_correctas,
               count(*) FILTER (WHERE a.abstencion_correcta)             AS abstencion_correcta,
               avg(a.relevancia)::float                                  AS relevancia_prom,
               coalesce(sum(a.costo_usd), 0)::float                      AS costo_usd,
               avg(a.segundos) FILTER (WHERE NOT a.desde_cache)          AS segundos_prom
          FROM auditorias a JOIN mensajes m ON m.id = a.mensaje_id
         WHERE true {filtro}"""
    async with grupo.connection() as conexion:
        fila = await (await conexion.execute(consulta, valores)).fetchone()
        fila["auditoria"] = await (await conexion.execute(consulta_auditorias, valores)).fetchone()
        return fila


async def por_dia(grupo: AsyncConnectionPool, desde: datetime | None, hasta: datetime | None
                  ) -> list[dict]:
    """Preguntas y tiempo promedio por día, para la gráfica."""
    filtro, valores = _filtro_fechas(desde, hasta)
    consulta = f"""
        SELECT date_trunc('day', m.creado_en)::date AS dia, count(*) AS preguntas,
               avg(x.segundos_total) AS segundos_prom,
               sum(x.tokens_entrada) AS tokens_entrada, sum(x.tokens_salida) AS tokens_salida
          FROM mensajes m JOIN metricas x ON x.mensaje_id = m.id
         WHERE m.rol = 'agente' {filtro}
         GROUP BY 1 ORDER BY 1"""
    async with grupo.connection() as conexion:
        return await (await conexion.execute(consulta, valores)).fetchall()


async def lista_de_preguntas(grupo: AsyncConnectionPool, desde: datetime | None,
                             hasta: datetime | None, texto: str | None, limite: int,
                             desplazamiento: int) -> list[dict]:
    """Pregunta, respuesta y métricas de cada turno, del más nuevo al más viejo."""
    filtro, valores = _filtro_fechas(desde, hasta)
    if texto:
        filtro += " AND (p.contenido ILIKE %s OR m.contenido ILIKE %s)"
        valores += [f"%{texto}%", f"%{texto}%"]
    # La pregunta es el mensaje del usuario inmediatamente anterior en la misma conversación
    consulta = f"""
        SELECT m.id, m.creado_en, m.conversacion_id, p.contenido AS pregunta,
               m.contenido AS respuesta, x.tokens_entrada, x.tokens_salida, x.segundos_total,
               x.segundos_busqueda, x.se_puede_responder, x.desde_cache, x.llamadas_llm,
               x.tools, x.consulta_en, x.error, x.modelo, {_AUDITORIA_JSON}
          FROM mensajes m
          JOIN metricas x ON x.mensaje_id = m.id
          JOIN LATERAL (SELECT contenido FROM mensajes
                         WHERE conversacion_id = m.conversacion_id AND id < m.id AND rol = 'usuario'
                         ORDER BY id DESC LIMIT 1) p ON true
         WHERE m.rol = 'agente' {filtro}
         ORDER BY m.id DESC LIMIT %s OFFSET %s"""
    async with grupo.connection() as conexion:
        return await (await conexion.execute(consulta, valores + [limite, desplazamiento])
                      ).fetchall()
