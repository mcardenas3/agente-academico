// Cómo habla la interfaz con la API de FastAPI

export type Cita = { id: string; seccion: string; paginas: number[] }

export type Respuesta = {
  respuesta: string
  se_puede_responder: boolean
  citas: Cita[]
  consulta_en: string | null
  tokens_entrada: number
  tokens_salida: number
  segundos: number
  segundos_busqueda: number
  desde_cache: boolean
  error: string | null
}

export type MensajeGuardado = { id: number; rol: 'usuario' | 'agente'; contenido: string; citas: Cita[] }

type Manejadores = {
  conversacion: (id: string) => void
  buscando: (consulta: string) => void
  respuesta: (r: Respuesta) => void
  error: (mensaje: string) => void
}

/** Manda la pregunta y va llamando a los manejadores conforme llegan los eventos SSE. */
export async function preguntar(pregunta: string, conversacionId: string | null, m: Manejadores) {
  let res: Response
  try {
    res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pregunta, conversacion_id: conversacionId }),
    })
  } catch {
    m.error('No hay conexión con el servidor.')
    return
  }
  if (!res.ok || !res.body) {
    m.error(`El servidor respondió con un error (${res.status}).`)
    return
  }
  // Un evento SSE son líneas "event: …" y "data: …" separadas por una línea en blanco
  const lector = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let pendiente = ''
  let terminado = false
  while (true) {
    const { value, done } = await lector.read()
    if (done) break
    pendiente += value
    const bloques = pendiente.split('\n\n')
    pendiente = bloques.pop() ?? ''
    for (const bloque of bloques) {
      let evento = 'message'
      let datos = ''
      for (const linea of bloque.split('\n')) {
        if (linea.startsWith('event:')) evento = linea.slice(6).trim()
        else if (linea.startsWith('data:')) datos += linea.slice(5).trim()
      }
      if (!datos) continue
      const d = JSON.parse(datos)
      if (evento === 'conversacion') m.conversacion(d.id)
      else if (evento === 'buscando') m.buscando(d.argumentos?.consulta_en ?? '')
      else if (evento === 'respuesta') { m.respuesta(d); terminado = true }
      else if (evento === 'error') { m.error(d.mensaje); terminado = true }
    }
  }
  if (!terminado) m.error('La conexión se cortó antes de terminar.')
}

export type Fragmento = { id: string; seccion: string; paginas: number[]; texto: string }

/** El texto exacto de los fragmentos citados, en el mismo orden. */
export async function cargarFragmentos(ids: string[]): Promise<Fragmento[]> {
  const qs = new URLSearchParams(ids.map((id) => ['ids', id]))
  const res = await fetch(`/api/fragmentos?${qs}`)
  if (!res.ok) throw new Error(`Error ${res.status}`)
  return res.json()
}

export async function cargarConversacion(id: string): Promise<MensajeGuardado[]> {
  const res = await fetch(`/api/conversaciones/${id}`)
  return res.ok ? res.json() : []
}

// --- Panel /admin ---

export type Resumen = {
  preguntas: number
  conversaciones: number
  tokens_entrada: number
  tokens_salida: number
  tokens_entrada_prom: number | null
  tokens_salida_prom: number | null
  segundos_prom: number | null
  segundos_prom_sin_cache: number | null
  segundos_prom_cache: number | null
  segundos_busqueda_prom: number | null
  segundos_llm_prom: number | null
  desde_cache: number
  sin_respuesta: number
  errores: number
}

export type Dia = { dia: string; preguntas: number; segundos_prom: number; tokens_entrada: number; tokens_salida: number }

export type FilaPregunta = {
  id: number
  creado_en: string
  pregunta: string
  respuesta: string
  tokens_entrada: number
  tokens_salida: number
  segundos_total: number
  segundos_busqueda: number
  se_puede_responder: boolean
  desde_cache: boolean
  llamadas_llm: number
  tools: string[]
  consulta_en: string | null
  error: string | null
  modelo: string
}

export class NoAutorizado extends Error {}

export async function admin<T>(ruta: string, token: string, parametros: Record<string, string | number | undefined>): Promise<T> {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(parametros)) if (v !== undefined && v !== '') qs.set(k, String(v))
  const res = await fetch(`/api/admin/${ruta}?${qs}`, { headers: { Authorization: `Bearer ${token}` } })
  if (res.status === 401 || res.status === 503) throw new NoAutorizado((await res.json()).detail)
  if (!res.ok) throw new Error(`Error ${res.status}`)
  return res.json()
}
