import { useCallback, useEffect, useRef, useState } from 'react'
import {
  cargarConversacion, cargarFragmentos, listarConversaciones, ocultarConversacion, preguntar,
  type Auditoria, type Cita, type ConversacionResumen, type Fragmento, type Respuesta,
} from './api'
import { paginas, numero, tiempo, ultimaSeccion } from './formato'
import Historial from './Historial'
import { VEREDICTOS } from './piezas'
import {
  ArrowClockwise, ArrowSquareOut, ArrowUp, CaretDown, Check, Lightning, List, MagnifyingGlass, NotePencil, Quotes,
  ShieldCheck, ShieldWarning, Warning,
} from './iconos'

// La auditoría de Claude: 'pendiente' mientras llega, null si no se pudo, undefined si nunca se pidió
type EstadoAuditoria = Auditoria | 'pendiente' | null | undefined

type Mensaje =
  | { rol: 'usuario'; texto: string }
  | { rol: 'agente'; id?: number; texto: string; citas: Cita[]; meta?: Respuesta; auditoria?: EstadoAuditoria }
  | { rol: 'pensando'; busqueda: string | null; verificando?: { intento: number; correcciones: number } }
  | { rol: 'error'; texto: string; pregunta: string }

const SUGERENCIAS = [
  '¿Cuáles son las fases del paradigma RAG?',
  '¿Qué es HyDE y cómo mejora la recuperación?',
  '¿Para qué sirve el re-ranking?',
  'How does RAPTOR organize documents?',
]

const LLAVE_CONVERSACION = 'conversacion_id'

/** Qué mostrar de la verificación de Claude al llegar una respuesta. */
function estadoAuditoria(r: Respuesta): EstadoAuditoria {
  if (r.auditoria) return { ...r.auditoria, correcciones: r.correcciones }
  if (r.auditando) return 'pendiente'
  if (r.verificacion === 'inactiva') return undefined
  return null  // Claude no respondió o se llegó al tope diario: se muestra como "sin verificar"
}

function leerConversacion() {
  try { return localStorage.getItem(LLAVE_CONVERSACION) } catch { return null }
}
function guardarConversacion(id: string | null) {
  try { id ? localStorage.setItem(LLAVE_CONVERSACION, id) : localStorage.removeItem(LLAVE_CONVERSACION) } catch { /* nada */ }
}

export default function Chat() {
  const [mensajes, setMensajes] = useState<Mensaje[]>([])
  const [conversacion, setConversacion] = useState<string | null>(leerConversacion)
  const [texto, setTexto] = useState('')
  const [ocupado, setOcupado] = useState(false)
  const [historial, setHistorial] = useState<ConversacionResumen[]>([])
  const [historialAbierto, setHistorialAbierto] = useState(false)  // solo cuenta en celular
  const final = useRef<HTMLDivElement>(null)
  const entrada = useRef<HTMLTextAreaElement>(null)
  // Número de la pregunta en curso: la auditoría llega después de la respuesta y para entonces
  // ya se puede haber mandado otra; así una petición vieja no desbloquea a la nueva
  const peticion = useRef(0)

  const refrescarHistorial = useCallback(() => { listarConversaciones().then(setHistorial) }, [])

  // Pinta una conversación guardada (sus mensajes con sus citas)
  async function cargar(id: string) {
    const guardados = await cargarConversacion(id)
    setMensajes(guardados.map((m) => (m.rol === 'usuario'
      ? { rol: 'usuario', texto: m.contenido }
      : { rol: 'agente', id: m.id, texto: m.contenido, citas: m.citas, auditoria: m.auditoria ?? undefined })))
  }

  // Al abrir, se recupera la última conversación (si la hay) y el historial
  useEffect(() => {
    const id = leerConversacion()
    if (id) void cargar(id)
    refrescarHistorial()
  }, [refrescarHistorial])

  useEffect(() => { final.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [mensajes])

  // Reemplaza el último mensaje (el de "pensando") por lo que llegó
  const reemplazarUltimo = (m: Mensaje) => setMensajes((ms) => [...ms.slice(0, -1), m])

  async function enviar(pregunta: string) {
    pregunta = pregunta.trim()
    if (!pregunta || ocupado) return
    setTexto('')
    setOcupado(true)
    const mia = ++peticion.current
    let liberada = false
    // Se desbloquea la caja al llegar la respuesta, sin esperar la auditoría
    const liberar = () => {
      if (liberada || peticion.current !== mia) return
      liberada = true
      setOcupado(false)
      refrescarHistorial()  // la conversación aparece (o sube) en el historial
      entrada.current?.focus()
    }
    setMensajes((ms) => [...ms.filter((m) => m.rol !== 'error'), { rol: 'usuario', texto: pregunta },
      { rol: 'pensando', busqueda: null }])
    await preguntar(pregunta, conversacion, {
      conversacion: (id) => { setConversacion(id); guardarConversacion(id) },
      buscando: (consulta) => reemplazarUltimo({ rol: 'pensando', busqueda: consulta }),
      verificando: (intento, correcciones) => setMensajes((ms) => {
        const ultimo = ms[ms.length - 1]
        const busqueda = ultimo?.rol === 'pensando' ? ultimo.busqueda : null
        return [...ms.slice(0, -1), { rol: 'pensando', busqueda, verificando: { intento, correcciones } }]
      }),
      respuesta: (r) => {
        reemplazarUltimo({ rol: 'agente', id: r.mensaje_id, texto: r.respuesta, citas: r.citas, meta: r,
          auditoria: estadoAuditoria(r) })
        liberar()
      },
      error: (mensaje) => reemplazarUltimo({ rol: 'error', texto: mensaje, pregunta }),
      auditoria: (id, a) => setMensajes((ms) => ms.map((m) => (m.rol === 'agente' && m.id === id ? { ...m, auditoria: a } : m))),
    })
    liberar()  // por si terminó en error (sin respuesta)
  }

  async function abrirConversacion(id: string) {
    setHistorialAbierto(false)
    if (ocupado || id === conversacion) return
    setConversacion(id)
    guardarConversacion(id)
    await cargar(id)
  }

  async function quitarDelHistorial(id: string) {
    if (!window.confirm('¿Quitar esta conversación del historial?')) return
    await ocultarConversacion(id)
    if (id === conversacion) nuevaConversacion()
    refrescarHistorial()
  }

  function reintentar(pregunta: string) {
    // Se quita la pregunta fallida para no duplicarla y se manda otra vez
    setMensajes((ms) => ms.slice(0, -2))
    void enviar(pregunta)
  }

  function nuevaConversacion() {
    setMensajes([])
    setConversacion(null)
    guardarConversacion(null)
    setHistorialAbierto(false)
    entrada.current?.focus()
  }

  return (
    <div className="flex h-full">
    <Historial conversaciones={historial} activa={conversacion} ocupado={ocupado} abierto={historialAbierto}
      onCerrar={() => setHistorialAbierto(false)} onNueva={nuevaConversacion}
      onAbrir={(id) => void abrirConversacion(id)} onOcultar={(id) => void quitarDelHistorial(id)} />
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6">
          {mensajes.length === 0 ? (
            <Bienvenida onElegir={enviar} />
          ) : (
            <div className="flex flex-col gap-6">
              {mensajes.map((m, i) => <Burbuja key={i} m={m} onReintentar={reintentar} />)}
            </div>
          )}
          <div ref={final} />
        </div>
      </div>

      <div className="shrink-0 border-t border-line-subtle bg-canvas">
        <form
          className="mx-auto flex w-full max-w-3xl items-end gap-2 px-4 py-3 sm:px-6"
          onSubmit={(e) => { e.preventDefault(); void enviar(texto) }}
        >
          {/* En celular, el historial se abre con este botón (en escritorio siempre está visible) */}
          <button type="button" className="btn btn-ghost h-11 px-3 md:hidden" onClick={() => setHistorialAbierto(true)}
            title="Historial" aria-label="Abrir historial">
            <List size={18} weight="bold" />
          </button>
          {mensajes.length > 0 && (
            <button type="button" className="btn btn-ghost h-11 px-3 md:hidden" onClick={nuevaConversacion}
              disabled={ocupado} title="Nueva conversación" aria-label="Nueva conversación">
              <NotePencil size={18} weight="bold" />
            </button>
          )}
          <textarea
            ref={entrada}
            className="campo max-h-40 min-h-11 flex-1 resize-none py-2.5"
            rows={1}
            maxLength={1000}
            placeholder="Pregunta algo sobre el artículo…"
            value={texto}
            autoFocus
            onChange={(e) => setTexto(e.target.value)}
            onKeyDown={(e) => {
              // Enter manda; Shift+Enter hace salto de línea
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void enviar(texto) }
            }}
          />
          <button type="submit" className="btn btn-primary h-11 px-3.5" disabled={ocupado || !texto.trim()}
            aria-label="Enviar">
            <ArrowUp size={18} weight="bold" />
          </button>
        </form>
      </div>
    </div>
    </div>
  )
}

function Bienvenida({ onElegir }: { onElegir: (p: string) => void }) {
  return (
    <div className="flex flex-col items-center pt-[12vh] text-center">
      <h1 className="text-h2 font-bold tracking-tight text-ink">Pregúntale al artículo</h1>
      <p className="mt-2 max-w-md text-body text-ink-muted">
        Respuestas con base en{' '}
        <a href="/articulo.pdf" target="_blank" rel="noopener" className="text-brand-ink underline-offset-2 hover:underline">
          <em>A Survey on Retrieval-Augmented Text Generation for Large Language Models</em>
        </a>, con la sección y la página de donde salen.
      </p>
      <div className="mt-8 grid w-full max-w-xl gap-2 sm:grid-cols-2">
        {SUGERENCIAS.map((s) => (
          <button key={s} onClick={() => onElegir(s)}
            className="card px-4 py-3 text-left text-meta text-ink-muted transition-colors hover:border-brand-line hover:text-ink">
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

function Burbuja({ m, onReintentar }: { m: Mensaje; onReintentar: (p: string) => void }) {
  if (m.rol === 'usuario') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-sunken px-4 py-2.5 text-body text-ink">
          {m.texto}
        </div>
      </div>
    )
  }

  if (m.rol === 'pensando') {
    const v = m.verificando
    return (
      <div className="flex flex-col gap-1.5 text-meta text-ink-muted" aria-live="polite">
        {m.busqueda === null && !v ? (
          <Paso activo icono={<MagnifyingGlass size={14} weight="bold" />}>Buscando en el artículo</Paso>
        ) : (
          <>
            {m.busqueda !== null && (
              <Paso icono={<Check size={14} weight="bold" />}>
                Buscó: <span className="text-ink">“{m.busqueda}”</span>
              </Paso>
            )}
            {!v && <Paso activo icono={<NotePencil size={14} weight="bold" />}>Redactando la respuesta</Paso>}
            {v && v.correcciones === 0 && (
              <>
                <Paso icono={<Check size={14} weight="bold" />}>Redactó la respuesta</Paso>
                <Paso activo icono={<ShieldCheck size={14} weight="bold" />}>Claude está verificando que todo salga del artículo</Paso>
              </>
            )}
            {v && v.correcciones > 0 && (
              <>
                <Paso icono={<ShieldWarning size={14} weight="bold" className="text-[var(--amber-11)]" />}>
                  Claude encontró afirmaciones sin respaldo y la regresó para corregir
                </Paso>
                <Paso activo icono={<ShieldCheck size={14} weight="bold" />}>Verificando la respuesta corregida</Paso>
              </>
            )}
          </>
        )}
      </div>
    )
  }

  if (m.rol === 'error') {
    return (
      <div className="card flex items-center gap-3 px-4 py-3" data-tone="bad">
        <Warning size={18} weight="bold" className="shrink-0" />
        <span className="flex-1 text-meta">{m.texto}</span>
        <button className="btn" onClick={() => onReintentar(m.pregunta)}>
          <ArrowClockwise size={14} weight="bold" /> Reintentar
        </button>
      </div>
    )
  }

  const fuera = m.meta && !m.meta.se_puede_responder
  return (
    <div className="flex flex-col gap-2.5">
      <div className="whitespace-pre-wrap text-body text-ink">{m.texto}</div>
      {m.citas.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {agruparPorSeccion(m.citas).map((c) => {
            // Abre el PDF en la primera página del texto citado (el visor del navegador entiende #page)
            const pagina = Math.min(...c.paginas)
            return (
              <a key={c.seccion} className="chip cita" data-tone="brand" href={`/articulo.pdf#page=${pagina}`}
                target="_blank" rel="noopener" title={`${c.seccion} — abrir el PDF en la página ${pagina}`}>
                {ultimaSeccion(c.seccion)} · {paginas(c.paginas)}
                <ArrowSquareOut size={11} weight="bold" />
              </a>
            )
          })}
        </div>
      )}
      {m.citas.length > 0 && <TextoCitado citas={m.citas} />}
      {m.meta && (
        <div className="flex flex-wrap items-center gap-2 text-micro text-ink-faint">
          {fuera && <span className="chip" data-tone="warn">Fuera del artículo</span>}
          {m.meta.desde_cache ? (
            <span className="inline-flex items-center gap-1"><Lightning size={12} weight="fill" /> Desde caché · {tiempo(m.meta.segundos)}</span>
          ) : (
            <span>{tiempo(m.meta.segundos)} · {numero(m.meta.tokens_entrada)} → {numero(m.meta.tokens_salida)} tokens</span>
          )}
        </div>
      )}
      {m.auditoria !== undefined && <Auditado estado={m.auditoria} />}
    </div>
  )
}

const FIDELIDAD = { fiel: 'Fiel', parcialmente_fiel: 'Parcialmente fiel', no_fiel: 'No fiel' } as const

/** Lo que dijo Claude al revisar la respuesta contra los fragmentos. Se despliega al tocarlo. */
function Auditado({ estado }: { estado: Auditoria | 'pendiente' | null }) {
  const [abierto, setAbierto] = useState(false)

  if (estado === 'pendiente') {
    return (
      <div className="text-micro text-ink-muted" aria-live="polite">
        <Paso activo icono={<ShieldCheck size={13} weight="bold" />}>Claude está auditando la respuesta</Paso>
      </div>
    )
  }
  if (estado === null) {
    return (
      <p className="inline-flex items-center gap-1.5 text-micro text-ink-faint">
        <ShieldWarning size={13} weight="bold" /> Sin verificar con Claude
      </p>
    )
  }

  const v = VEREDICTOS[estado.veredicto]
  const Icono = estado.veredicto === 'aprobada' ? ShieldCheck : ShieldWarning
  const corregida = (estado.correcciones ?? 0) > 0
  return (
    <div>
      <button onClick={() => setAbierto((a) => !a)} aria-expanded={abierto}
        className="inline-flex flex-wrap items-center gap-1.5 text-left text-micro font-semibold text-ink-muted hover:text-ink">
        <span className="chip" data-tone={v.tono}><Icono size={12} weight="bold" /> {v.texto}</span>
        Verificada por Claude{corregida ? ' · corregida antes de mostrarse' : ''}
        <CaretDown size={11} weight="bold" className={`transition-transform ${abierto ? 'rotate-180' : ''}`} />
      </button>
      {abierto && (
        <div className="mt-2 flex flex-col gap-2 rounded-[10px] border border-line-subtle bg-raised px-3 py-2.5 text-meta text-ink">
          <p className="m-0">{estado.resumen}</p>
          <div className="flex flex-wrap gap-1.5 text-micro">
            <span className="chip" data-tone={estado.fidelidad === 'fiel' ? 'ok' : estado.fidelidad === 'no_fiel' ? 'bad' : 'warn'}>
              {FIDELIDAD[estado.fidelidad]}
            </span>
            <span className="chip" data-tone={estado.relevancia >= 4 ? 'ok' : estado.relevancia >= 3 ? 'warn' : 'bad'}>
              Relevancia {estado.relevancia}/5
            </span>
            <span className="chip" data-tone={estado.citas_correctas ? 'ok' : 'bad'}>
              {estado.citas_correctas ? 'Citas correctas' : 'Citas incorrectas'}
            </span>
            <span className="chip" data-tone={estado.abstencion_correcta ? 'ok' : 'bad'}>
              {estado.abstencion_correcta ? 'Acertó en responder o abstenerse' : 'No acertó en responder o abstenerse'}
            </span>
            {estado.responde_lo_pedido != null && (
              <span className="chip" data-tone={estado.responde_lo_pedido ? 'ok' : 'warn'}>
                {estado.responde_lo_pedido ? 'Responde lo pedido' : 'No responde todo lo pedido'}
              </span>
            )}
          </div>
          {estado.problemas.length > 0 && (
            <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-ink-muted">
              {estado.problemas.map((p) => <li key={p}>{p}</li>)}
            </ul>
          )}
          <p className="m-0 text-micro text-ink-faint">
            Revisión hecha con {estado.modelo} contra los fragmentos que recuperó la búsqueda
            {corregida ? `, después de que el agente corrigió lo que Claude le regresó` : ''}
            {estado.desde_cache ? ' · reusada de la caché, sin costo' : ` · costó US$${estado.costo_usd.toFixed(4)}`}
          </p>
        </div>
      )}
    </div>
  )
}

/** Despliega el texto exacto de los fragmentos en que se basó la respuesta. Se pide al abrirlo. */
function TextoCitado({ citas }: { citas: Cita[] }) {
  const [abierto, setAbierto] = useState(false)
  const [fragmentos, setFragmentos] = useState<Fragmento[] | null>(null)
  const [error, setError] = useState(false)

  function alternar() {
    setAbierto((a) => !a)
    if (!fragmentos) {
      setError(false)
      cargarFragmentos(citas.map((c) => c.id)).then(setFragmentos).catch(() => setError(true))
    }
  }

  return (
    <div>
      <button onClick={alternar} aria-expanded={abierto}
        className="inline-flex items-center gap-1.5 text-micro font-semibold text-ink-muted hover:text-ink">
        <Quotes size={13} weight="bold" />
        {abierto ? 'Ocultar' : 'Ver'} el texto citado ({citas.length})
        <CaretDown size={11} weight="bold" className={`transition-transform ${abierto ? 'rotate-180' : ''}`} />
      </button>
      {abierto && (
        <div className="mt-2 flex flex-col gap-2">
          {error && <p className="text-micro text-[var(--red-11)]">No se pudo cargar el texto citado.</p>}
          {!fragmentos && !error && <p className="text-micro text-ink-faint">Cargando…</p>}
          {fragmentos?.map((f) => {
            // Las tablas vienen en Markdown: se muestran en monoespaciada y con scroll horizontal
            const esTabla = /^\|/m.test(f.texto)
            return (
              <figure key={f.id} className="m-0 rounded-[10px] border border-line-subtle bg-raised">
                <figcaption className="flex items-center gap-2 border-b border-line-subtle px-3 py-1.5 text-micro text-ink-muted">
                  <span className="min-w-0 truncate" title={f.seccion}>{f.seccion}</span>
                  <a className="ml-auto inline-flex shrink-0 items-center gap-1 font-semibold text-brand-ink hover:underline"
                    href={`/articulo.pdf#page=${Math.min(...f.paginas)}`} target="_blank" rel="noopener">
                    {paginas(f.paginas)} <ArrowSquareOut size={11} weight="bold" />
                  </a>
                </figcaption>
                <blockquote className={`m-0 max-h-64 overflow-auto px-3 py-2 text-ink ${esTabla
                  ? 'font-mono text-[11px] leading-snug whitespace-pre'
                  : 'text-meta whitespace-pre-wrap'}`}>
                  {f.texto}
                </blockquote>
              </figure>
            )
          })}
        </div>
      )}
    </div>
  )
}

/** Varios fragmentos de la misma sección se muestran como una sola cita con todas sus páginas. */
function agruparPorSeccion(citas: Cita[]) {
  const porSeccion = new Map<string, Set<number>>()
  for (const c of citas) {
    const conjunto = porSeccion.get(c.seccion) ?? new Set<number>()
    c.paginas.forEach((p) => conjunto.add(p))
    porSeccion.set(c.seccion, conjunto)
  }
  return [...porSeccion].map(([seccion, ps]) => ({ seccion, paginas: [...ps] }))
}

function Paso({ icono, activo, children }: { icono: React.ReactNode; activo?: boolean; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className={activo ? 'text-brand-ink' : 'text-ink-faint'}>{icono}</span>
      <span>{children}</span>
      {activo && <span className="latido ml-0.5 inline-flex gap-0.5"><span>•</span><span>•</span><span>•</span></span>}
    </div>
  )
}
