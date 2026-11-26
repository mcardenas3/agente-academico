import { useCallback, useEffect, useRef, useState } from 'react'
import {
  articuloDe, cargarConversacion, cargarFragmentos, listarArticulos, listarConversaciones, ocultarConversacion,
  preguntar, urlPdf, type Articulo, type Cita, type ConversacionResumen, type Fragmento, type Respuesta,
} from './api'
import { paginas, numero, tiempo, ultimaSeccion } from './formato'
import Historial from './Historial'
import {
  ArrowClockwise, ArrowSquareOut, ArrowUp, BookOpenText, CaretDown, Check, Lightning, List, MagnifyingGlass,
  NotePencil, Quotes, Warning,
} from './iconos'

type Mensaje =
  | { rol: 'usuario'; texto: string }
  | { rol: 'agente'; texto: string; citas: Cita[]; meta?: Respuesta }
  | { rol: 'pensando'; busqueda: string | null }
  | { rol: 'error'; texto: string; pregunta: string }

// Sugerencias para el survey original; para los artículos subidos van unas que sirven para cualquiera
const SUGERENCIAS_ORIGINAL = [
  '¿Cuáles son las fases del paradigma RAG?',
  '¿Qué es HyDE y cómo mejora la recuperación?',
  '¿Para qué sirve el re-ranking?',
  'How does RAPTOR organize documents?',
]
const SUGERENCIAS_GENERALES = [
  '¿De qué trata este artículo?',
  '¿Cuáles son sus principales conclusiones?',
  '¿Qué métodos propone o compara?',
  '¿Qué limitaciones menciona?',
]

const LLAVE_CONVERSACION = 'conversacion_id'

function leerConversacion() {
  try { return localStorage.getItem(LLAVE_CONVERSACION) } catch { return null }
}
function guardarConversacion(id: string | null) {
  try { id ? localStorage.setItem(LLAVE_CONVERSACION, id) : localStorage.removeItem(LLAVE_CONVERSACION) } catch { /* nada */ }
}

/** Título corto para un chip: «A Survey on Retrieval-Augmented…» */
const corto = (titulo: string) => (titulo.length > 34 ? `${titulo.slice(0, 33).trimEnd()}…` : titulo)

export default function Chat() {
  const [mensajes, setMensajes] = useState<Mensaje[]>([])
  const [conversacion, setConversacion] = useState<string | null>(leerConversacion)
  const [texto, setTexto] = useState('')
  const [ocupado, setOcupado] = useState(false)
  const [historial, setHistorial] = useState<ConversacionResumen[]>([])
  const [historialAbierto, setHistorialAbierto] = useState(false)  // solo cuenta en celular
  const [articulos, setArticulos] = useState<Articulo[]>([])
  // Artículo de la conversación actual (o el elegido para la nueva); null = todos los artículos
  const [elegido, setElegido] = useState<string | null>(null)
  const final = useRef<HTMLDivElement>(null)
  const entrada = useRef<HTMLTextAreaElement>(null)

  const refrescarHistorial = useCallback(() => { listarConversaciones().then(setHistorial) }, [])
  const tituloDe = useCallback((id: string) => articulos.find((a) => a.id === id)?.titulo ?? id, [articulos])

  // Pinta una conversación guardada (sus mensajes con sus citas)
  async function cargar(id: string) {
    const guardados = await cargarConversacion(id)
    setMensajes(guardados.map((m) => (m.rol === 'usuario'
      ? { rol: 'usuario', texto: m.contenido }
      : { rol: 'agente', texto: m.contenido, citas: m.citas })))
  }

  // Al abrir: la biblioteca, el historial y la última conversación (si la hay)
  useEffect(() => {
    const id = leerConversacion()
    if (id) void cargar(id)
    listarArticulos().then(setArticulos)
    refrescarHistorial()
  }, [refrescarHistorial])

  // Cuando llega el historial, la conversación recuperada toma su artículo
  useEffect(() => {
    const actual = historial.find((c) => c.id === conversacion)
    if (actual) setElegido(actual.articulo_id)
  }, [historial, conversacion])

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
    await preguntar(pregunta, conversacion, elegido, {
      conversacion: (id, articuloId) => { setConversacion(id); guardarConversacion(id); setElegido(articuloId) },
      buscando: (consulta) => reemplazarUltimo({ rol: 'pensando', busqueda: consulta }),
      respuesta: (r) => reemplazarUltimo({ rol: 'agente', texto: r.respuesta, citas: r.citas, meta: r }),
      error: (mensaje) => reemplazarUltimo({ rol: 'error', texto: mensaje, pregunta }),
    })
    setOcupado(false)
    refrescarHistorial()  // la conversación aparece (o sube) en el historial
    entrada.current?.focus()
  }

  async function abrirConversacion(id: string) {
    setHistorialAbierto(false)
    if (ocupado || id === conversacion) return
    setConversacion(id)
    guardarConversacion(id)
    setElegido(historial.find((c) => c.id === id)?.articulo_id ?? null)
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
    listarArticulos().then(setArticulos)  // por si se subió uno mientras tanto
    entrada.current?.focus()
  }

  // Con varios artículos y sin uno elegido, cada cita dice de qué artículo sale
  const mostrarArticulo = elegido === null && articulos.length > 1
  const vacia = mensajes.length === 0

  return (
    <div className="flex h-full">
    <Historial conversaciones={historial} activa={conversacion} ocupado={ocupado} abierto={historialAbierto}
      onCerrar={() => setHistorialAbierto(false)} onNueva={nuevaConversacion}
      onAbrir={(id) => void abrirConversacion(id)} onOcultar={(id) => void quitarDelHistorial(id)} />
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:px-6">
          {vacia ? (
            <Bienvenida articulos={articulos} elegido={elegido} onElegirArticulo={setElegido} onPreguntar={enviar} />
          ) : (
            <div className="flex flex-col gap-6">
              <Alcance articulos={articulos} elegido={elegido} />
              {mensajes.map((m, i) => (
                <Burbuja key={i} m={m} onReintentar={reintentar} mostrarArticulo={mostrarArticulo} tituloDe={tituloDe} />
              ))}
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
          {!vacia && (
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
            placeholder={elegido === null && articulos.length > 1 ? 'Pregunta algo sobre los artículos…' : 'Pregunta algo sobre el artículo…'}
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

function Bienvenida({ articulos, elegido, onElegirArticulo, onPreguntar }: {
  articulos: Articulo[]
  elegido: string | null
  onElegirArticulo: (id: string | null) => void
  onPreguntar: (p: string) => void
}) {
  const articulo = articulos.find((a) => a.id === elegido)
  const varios = articulos.length > 1
  const sugerencias = articulo && !articulo.original ? SUGERENCIAS_GENERALES : SUGERENCIAS_ORIGINAL
  return (
    <div className="flex flex-col items-center pt-[10vh] text-center">
      <h1 className="text-h2 font-bold tracking-tight text-ink">
        {varios && !articulo ? 'Pregúntale a la biblioteca' : 'Pregúntale al artículo'}
      </h1>
      {varios ? (
        <label className="mt-4 flex w-full max-w-xl items-center gap-2 text-left">
          <BookOpenText size={16} weight="bold" className="shrink-0 text-ink-faint" />
          <select className="campo py-2 text-meta" value={elegido ?? ''}
            onChange={(e) => onElegirArticulo(e.target.value || null)} aria-label="Artículo">
            <option value="">Todos los artículos ({articulos.length})</option>
            {articulos.map((a) => <option key={a.id} value={a.id}>{a.titulo}</option>)}
          </select>
        </label>
      ) : null}
      <p className="mt-3 max-w-md text-body text-ink-muted">
        {articulo || !varios ? (
          <>Respuestas con base en{' '}
            <a href={urlPdf(articulo?.id ?? articulos[0]?.id ?? 'huang2026-rag-survey')} target="_blank" rel="noopener"
              className="text-brand-ink underline-offset-2 hover:underline">
              <em>{articulo?.titulo ?? articulos[0]?.titulo ?? 'el artículo'}</em>
            </a>, con la sección y la página de donde salen.</>
        ) : (
          <>Busca en los {articulos.length} artículos a la vez; cada cita dice de cuál sale.</>
        )}
      </p>
      <div className="mt-8 grid w-full max-w-xl gap-2 sm:grid-cols-2">
        {sugerencias.map((s) => (
          <button key={s} onClick={() => onPreguntar(s)}
            className="card px-4 py-3 text-left text-meta text-ink-muted transition-colors hover:border-brand-line hover:text-ink">
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

/** Arriba de la conversación: sobre qué artículo se está preguntando. */
function Alcance({ articulos, elegido }: { articulos: Articulo[]; elegido: string | null }) {
  if (articulos.length <= 1) return null
  const articulo = articulos.find((a) => a.id === elegido)
  return (
    <div className="flex items-center gap-1.5 text-micro text-ink-faint">
      <BookOpenText size={13} weight="bold" />
      {articulo ? (
        <a href={urlPdf(articulo.id)} target="_blank" rel="noopener" className="truncate hover:text-ink hover:underline">
          {articulo.titulo}
        </a>
      ) : <span>Todos los artículos de la biblioteca</span>}
    </div>
  )
}

function Burbuja({ m, onReintentar, mostrarArticulo, tituloDe }: {
  m: Mensaje
  onReintentar: (p: string) => void
  mostrarArticulo: boolean
  tituloDe: (id: string) => string
}) {
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
          {agruparPorSeccion(m.citas).map((c) => {
            // Abre el PDF de su artículo en la primera página del texto citado (#page)
            const pagina = Math.min(...c.paginas)
            const titulo = tituloDe(c.articulo)
            return (
              <a key={`${c.articulo}|${c.seccion}`} className="chip cita" data-tone="brand" href={urlPdf(c.articulo, pagina)}
                target="_blank" rel="noopener" title={`${titulo} — ${c.seccion} — abrir el PDF en la página ${pagina}`}>
                {mostrarArticulo && <span className="font-normal opacity-80">{corto(titulo)} ·</span>}
                {ultimaSeccion(c.seccion)} · {paginas(c.paginas)}
                <ArrowSquareOut size={11} weight="bold" />
              </a>
            )
          })}
        </div>
      )}
      {m.citas.length > 0 && <TextoCitado citas={m.citas} mostrarArticulo={mostrarArticulo} />}
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

/** Despliega el texto exacto de los fragmentos en que se basó la respuesta. Se pide al abrirlo. */
function TextoCitado({ citas, mostrarArticulo }: { citas: Cita[]; mostrarArticulo: boolean }) {
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
                  <span className="min-w-0 truncate" title={`${f.articulo} — ${f.seccion}`}>
                    {mostrarArticulo && <span className="font-semibold text-ink">{corto(f.articulo)} · </span>}
                    {f.seccion}
                  </span>
                  <a className="ml-auto inline-flex shrink-0 items-center gap-1 font-semibold text-brand-ink hover:underline"
                    href={urlPdf(f.articulo_id, Math.min(...f.paginas))} target="_blank" rel="noopener">
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

/** Varios fragmentos de la misma sección (del mismo artículo) se muestran como una sola cita. */
function agruparPorSeccion(citas: Cita[]) {
  const grupos = new Map<string, { articulo: string; seccion: string; paginas: Set<number> }>()
  for (const c of citas) {
    const articulo = c.articulo_id ?? articuloDe(c.id)
    const llave = `${articulo}|${c.seccion}`
    const grupo = grupos.get(llave) ?? { articulo, seccion: c.seccion, paginas: new Set<number>() }
    c.paginas.forEach((p) => grupo.paginas.add(p))
    grupos.set(llave, grupo)
  }
  return [...grupos.values()].map((g) => ({ ...g, paginas: [...g.paginas] }))
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
