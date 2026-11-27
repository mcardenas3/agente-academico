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
  mensaje_id: number
  // Claude verifica antes de mostrar: 'verificada' (trae auditoria), 'fallida' (Claude no
  // respondió), 'omitida' (tope diario) o 'inactiva' (sin llave de Claude)
  verificacion: 'verificada' | 'fallida' | 'omitida' | 'inactiva'
  auditoria: Auditoria | null
  correcciones: number  // veces que Claude la regresó para corregirla
  auditando: boolean    // true = respuesta vieja de caché: su auditoría llega después
}

/** El veredicto de Claude sobre una respuesta, revisada contra los fragmentos recuperados. */
export type Auditoria = {
  veredicto: 'aprobada' | 'con_observaciones' | 'reprobada'
  fidelidad: 'fiel' | 'parcialmente_fiel' | 'no_fiel'
  relevancia: number
  citas_correctas: boolean
  abstencion_correcta: boolean
  problemas: string[]
  resumen: string
  analisis: string
  modelo: string
  costo_usd: number
  desde_cache: boolean
  correcciones?: number
}

export type MensajeGuardado = {
  id: number; rol: 'usuario' | 'agente'; contenido: string; citas: Cita[]; auditoria: Auditoria | null
}

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

export type ConversacionResumen = { id: string; titulo: string | null; actualizada_en: string; preguntas: number }

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
  conversacion: (id: string) => void
  buscando: (consulta: string) => void
  // Claude revisa la respuesta antes de mostrarla; correcciones > 0 = esta ya es la corregida
  verificando: (intento: number, correcciones: number) => void
  respuesta: (r: Respuesta) => void
  error: (mensaje: string) => void
  // Llega después de la respuesta: el veredicto, o null si no se pudo auditar
  auditoria: (mensajeId: number, a: Auditoria | null) => void
}

/** Manda la pregunta y va llamando a los manejadores conforme llegan los eventos SSE. */
export async function preguntar(pregunta: string, conversacionId: string | null, m: Manejadores) {
  let res: Response
  try {
    res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pregunta, conversacion_id: conversacionId, cliente_id: clienteId() }),
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
      else if (evento === 'verificando') m.verificando(d.intento, d.correcciones)
      else if (evento === 'respuesta') { m.respuesta(d); terminado = true }
      else if (evento === 'error') { m.error(d.mensaje); terminado = true }
      else if (evento === 'auditoria') m.auditoria(d.mensaje_id, d)
      else if (evento === 'auditoria_error' || evento === 'auditoria_omitida') m.auditoria(d.mensaje_id, null)
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
  corregidas: number
  auditoria: ResumenAuditoria
}

export type ResumenAuditoria = {
  auditadas: number
  aprobadas: number
  con_observaciones: number
  reprobadas: number
  fieles: number
  citas_correctas: number
  abstencion_correcta: number
  relevancia_prom: number | null
  costo_usd: number               // todo lo que se ha gastado en Claude en el periodo
  costo_prom_usd: number | null   // por respuesta verificada (sin contar las de caché)
  costo_max_usd: number | null    // la más cara (suele ser una corregida: dos revisiones)
  segundos_prom: number | null
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
  correcciones: number
  auditoria: Auditoria | null
}

// --- Vista de costos y desempeño (/admin/costos) ---

/** Totales de un periodo o de una conversación. El modelo local no cobra: de él van tokens,
 * llamadas y tiempo; lo que cuesta dinero es Claude (una llamada por verificación + una por
 * cada corrección). */
export type TotalesCosto = {
  preguntas: number
  desde_cache: number
  tokens_entrada: number
  tokens_salida: number
  llamadas_llm: number
  segundos_total: number
  segundos_prom: number | null
  verificaciones: number
  correcciones: number
  claude_tokens_entrada: number
  claude_tokens_salida: number
  costo_claude: number
  aprobadas: number
  con_observaciones: number
  reprobadas: number
}

export type CostosResumen = TotalesCosto & { conversaciones: number }

export type CostoConversacion = TotalesCosto & { id: string; titulo: string; inicio: string; ultima: string }

export type CostoPregunta = {
  id: number
  creado_en: string
  pregunta: string
  modelo: string
  tokens_entrada: number
  tokens_salida: number
  llamadas_llm: number
  segundos_total: number
  segundos_busqueda: number
  desde_cache: boolean
  correcciones: number
  se_puede_responder: boolean
  error: string | null
  modelo_auditor: string | null
  veredicto: Auditoria['veredicto'] | null
  claude_tokens_entrada: number | null
  claude_tokens_salida: number | null
  costo_claude: number | null
  segundos_claude: number | null
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
