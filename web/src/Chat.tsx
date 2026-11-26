import { useEffect, useRef, useState } from 'react'
import { cargarConversacion, preguntar, type Cita, type Respuesta } from './api'
import { paginas, numero, tiempo, ultimaSeccion } from './formato'
import { ArrowClockwise, ArrowUp, Check, Lightning, MagnifyingGlass, NotePencil, Warning } from './iconos'

type Mensaje =
  | { rol: 'usuario'; texto: string }
  | { rol: 'agente'; texto: string; citas: Cita[]; meta?: Respuesta }
  | { rol: 'pensando'; busqueda: string | null }
  | { rol: 'error'; texto: string; pregunta: string }

const SUGERENCIAS = [
  '¿Cuáles son las fases del paradigma RAG?',
  '¿Qué es HyDE y cómo mejora la recuperación?',
  '¿Para qué sirve el re-ranking?',
  'How does RAPTOR organize documents?',
]

const LLAVE_CONVERSACION = 'conversacion_id'

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
  const final = useRef<HTMLDivElement>(null)
  const entrada = useRef<HTMLTextAreaElement>(null)

  // Al abrir, se recupera la conversación anterior (si la hay)
  useEffect(() => {
    const id = leerConversacion()
    if (!id) return
    cargarConversacion(id).then((guardados) =>
      setMensajes(guardados.map((m) => (m.rol === 'usuario'
        ? { rol: 'usuario', texto: m.contenido }
        : { rol: 'agente', texto: m.contenido, citas: m.citas }))))
  }, [])

  useEffect(() => { final.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [mensajes])

  // Reemplaza el último mensaje (el de "pensando") por lo que llegó
  const reemplazarUltimo = (m: Mensaje) => setMensajes((ms) => [...ms.slice(0, -1), m])

  async function enviar(pregunta: string) {
    pregunta = pregunta.trim()
    if (!pregunta || ocupado) return
    setTexto('')
    setOcupado(true)
    setMensajes((ms) => [...ms.filter((m) => m.rol !== 'error'), { rol: 'usuario', texto: pregunta },
      { rol: 'pensando', busqueda: null }])
    await preguntar(pregunta, conversacion, {
      conversacion: (id) => { setConversacion(id); guardarConversacion(id) },
      buscando: (consulta) => reemplazarUltimo({ rol: 'pensando', busqueda: consulta }),
      respuesta: (r) => reemplazarUltimo({ rol: 'agente', texto: r.respuesta, citas: r.citas, meta: r }),
      error: (mensaje) => reemplazarUltimo({ rol: 'error', texto: mensaje, pregunta }),
    })
    setOcupado(false)
    entrada.current?.focus()
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
    entrada.current?.focus()
  }

  return (
    <div className="flex h-full flex-col">
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
          {mensajes.length > 0 && (
            <button type="button" className="btn btn-ghost h-11 px-3" onClick={nuevaConversacion}
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
  )
}

function Bienvenida({ onElegir }: { onElegir: (p: string) => void }) {
  return (
    <div className="flex flex-col items-center pt-[12vh] text-center">
      <h1 className="text-h2 font-bold tracking-tight text-ink">Pregúntale al artículo</h1>
      <p className="mt-2 max-w-md text-body text-ink-muted">
        Respuestas con base en <em>A Survey on Retrieval-Augmented Text Generation for Large Language
        Models</em>, con la sección y la página de donde salen.
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
    return (
      <div className="flex flex-col gap-1.5 text-meta text-ink-muted" aria-live="polite">
        {m.busqueda === null ? (
          <Paso activo icono={<MagnifyingGlass size={14} weight="bold" />}>Buscando en el artículo</Paso>
        ) : (
          <>
            <Paso icono={<Check size={14} weight="bold" />}>
              Buscó: <span className="text-ink">“{m.busqueda}”</span>
            </Paso>
            <Paso activo icono={<NotePencil size={14} weight="bold" />}>Redactando la respuesta</Paso>
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
          {agruparPorSeccion(m.citas).map((c) => (
            <span key={c.seccion} className="chip" data-tone="brand" title={c.seccion}>
              {ultimaSeccion(c.seccion)} · {paginas(c.paginas)}
            </span>
          ))}
        </div>
      )}
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
