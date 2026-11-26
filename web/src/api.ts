// Cómo habla la interfaz con la API de FastAPI

export type Cita = { id: string; seccion: string; paginas: number[]; articulo_id?: string | null }

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

// --- Historial sin login: cada navegador tiene un id aleatorio y ve solo sus conversaciones ---

const LLAVE_CLIENTE = 'cliente_id'
let clienteEnMemoria: string | null = null

function uuidAleatorio(): string {
  // crypto.randomUUID solo existe en páginas seguras (https o localhost); si no, se arma a mano
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  const b = crypto.getRandomValues(new Uint8Array(16))
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

/** El id de este navegador: se crea la primera vez y se guarda en localStorage. */
export function clienteId(): string {
  try {
    let id = localStorage.getItem(LLAVE_CLIENTE)
    if (!id) { id = uuidAleatorio(); localStorage.setItem(LLAVE_CLIENTE, id) }
    return id
  } catch {
    // Sin almacenamiento (modo privado estricto): vale solo mientras la página esté abierta
    return (clienteEnMemoria ??= uuidAleatorio())
  }
}

export type ConversacionResumen = {
  id: string; titulo: string | null; actualizada_en: string; preguntas: number; articulo_id: string | null
}

// --- Biblioteca de artículos ---

export type Articulo = { id: string; titulo: string; original: boolean; paginas: number | null; fragmentos: number | null }

export async function listarArticulos(): Promise<Articulo[]> {
  const res = await fetch('/api/articulos')
  return res.ok ? res.json() : []
}

/** De qué artículo es un fragmento: su id sin el número final ("huang2026-rag-survey-034"). */
export const articuloDe = (idFragmento: string) => idFragmento.replace(/-\d{3,}$/, '')

/** El PDF de un artículo abierto en una página. */
export const urlPdf = (articuloId: string, pagina?: number) =>
  `/articulos/${articuloId}.pdf${pagina ? `#page=${pagina}` : ''}`

export async function listarConversaciones(): Promise<ConversacionResumen[]> {
  const res = await fetch(`/api/conversaciones?cliente_id=${clienteId()}`)
  return res.ok ? res.json() : []
}

/** La quita del historial de este navegador (los datos se conservan para /admin). */
export async function ocultarConversacion(id: string): Promise<boolean> {
  const res = await fetch(`/api/conversaciones/${id}?cliente_id=${clienteId()}`, { method: 'DELETE' })
  return res.ok
}

type Manejadores = {
  conversacion: (id: string, articuloId: string | null) => void
  buscando: (consulta: string) => void
  respuesta: (r: Respuesta) => void
  error: (mensaje: string) => void
}

/** Manda la pregunta y va llamando a los manejadores conforme llegan los eventos SSE.
 * articuloId solo cuenta al empezar una conversación (null = todos los artículos). */
export async function preguntar(pregunta: string, conversacionId: string | null, articuloId: string | null,
  m: Manejadores) {
  let res: Response
  try {
    res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pregunta, conversacion_id: conversacionId, cliente_id: clienteId(), articulo_id: articuloId }),
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
      if (evento === 'conversacion') m.conversacion(d.id, d.articulo_id ?? null)
      else if (evento === 'buscando') m.buscando(d.argumentos?.consulta_en ?? '')
      else if (evento === 'respuesta') { m.respuesta(d); terminado = true }
      else if (evento === 'error') { m.error(d.mensaje); terminado = true }
    }
  }
  if (!terminado) m.error('La conexión se cortó antes de terminar.')
}

export type Fragmento = { id: string; seccion: string; paginas: number[]; texto: string; articulo_id: string; articulo: string }

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

export type ArticuloAdmin = Articulo & {
  estado: 'en_cola' | 'procesando' | 'listo' | 'error'
  progreso: string | null
  error: string | null
  subido_en: string
  procesado_en: string | null
}

/** Sube un PDF a la biblioteca; el trabajador lo procesa en segundo plano. */
export async function subirArticulo(token: string, archivo: File, titulo: string): Promise<ArticuloAdmin> {
  const datos = new FormData()
  datos.append('archivo', archivo)
  if (titulo.trim()) datos.append('titulo', titulo.trim())
  const res = await fetch('/api/admin/articulos', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: datos })
  if (res.status === 401) throw new NoAutorizado('Token incorrecto')
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `Error ${res.status}`)
  return res.json()
}

export async function borrarArticulo(token: string, id: string): Promise<void> {
  const res = await fetch(`/api/admin/articulos/${id}`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } })
  if (res.status === 401) throw new NoAutorizado('Token incorrecto')
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `Error ${res.status}`)
}

export async function admin<T>(ruta: string, token: string, parametros: Record<string, string | number | undefined>): Promise<T> {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(parametros)) if (v !== undefined && v !== '') qs.set(k, String(v))
  const res = await fetch(`/api/admin/${ruta}?${qs}`, { headers: { Authorization: `Bearer ${token}` } })
  if (res.status === 401 || res.status === 503) throw new NoAutorizado((await res.json()).detail)
  if (!res.ok) throw new Error(`Error ${res.status}`)
  return res.json()
}
