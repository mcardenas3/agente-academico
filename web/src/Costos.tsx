import { useCallback, useEffect, useState } from 'react'
import {
  admin, NoAutorizado, type CostoConversacion, type CostoPregunta, type CostosResumen, type TotalesCosto,
} from './api'
import { desdeDe, dolares, fecha, numero, pct, RANGOS, type Rango, tiempo } from './formato'
import { ArrowLeft, CaretDown, Lightning, SignOut } from './iconos'
import { Kpi, VEREDICTOS } from './piezas'

const POR_PAGINA = 25
// El nombre del modelo local para el texto de arriba; el de cada pregunta sale en su detalle
const MODELO_LOCAL = 'nemotron-3-nano:4b'
const ORDENES = [
  { id: 'reciente', etiqueta: 'Más recientes' },
  { id: 'costo', etiqueta: 'Más caras' },
  { id: 'tokens', etiqueta: 'Más tokens' },
  { id: 'tiempo', etiqueta: 'Más lentas' },
] as const

// Columnas de la tabla: en celular solo conversación y Claude; en escritorio todas
const COLUMNAS = 'grid grid-cols-[minmax(0,1fr)_6.5rem_1rem] sm:grid-cols-[minmax(0,1fr)_9rem_6.5rem_5rem_7.5rem_1rem] items-center gap-3'

/** Llamadas a Claude: una por respuesta verificada y una más por cada corrección. */
const llamadasClaude = (t: TotalesCosto) => t.verificaciones + t.correcciones

/** Vista para los admins: cuánto cuesta y cómo rinde cada conversación. */
export default function Costos({ token, onSalir, onVolver }: { token: string; onSalir: () => void; onVolver: () => void }) {
  const [rango, setRango] = useState<Rango>(RANGOS[3])
  const [orden, setOrden] = useState<(typeof ORDENES)[number]['id']>('reciente')
  const [resumen, setResumen] = useState<CostosResumen | null>(null)
  const [filas, setFilas] = useState<CostoConversacion[]>([])
  const [hayMas, setHayMas] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const desde = desdeDe(rango)

  const manejarError = useCallback((err: unknown) => {
    if (err instanceof NoAutorizado) onSalir()
    else setError('No se pudieron cargar los datos.')
  }, [onSalir])

  useEffect(() => {
    setError(null)
    admin<CostosResumen>('costos/resumen', token, { desde }).then(setResumen).catch(manejarError)
  }, [token, desde, manejarError])

  useEffect(() => {
    admin<CostoConversacion[]>('costos/conversaciones', token, { desde, orden, limite: POR_PAGINA })
      .then((f) => { setFilas(f); setHayMas(f.length === POR_PAGINA) }).catch(manejarError)
  }, [token, desde, orden, manejarError])

  function cargarMas() {
    admin<CostoConversacion[]>('costos/conversaciones', token, { desde, orden, limite: POR_PAGINA, desplazamiento: filas.length })
      .then((f) => { setFilas((a) => [...a, ...f]); setHayMas(f.length === POR_PAGINA) }).catch(manejarError)
  }

  const r = resumen
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
        <div className="flex flex-wrap items-center gap-3">
          <button className="btn btn-ghost px-2.5" onClick={onVolver} aria-label="Volver al panel" title="Volver al panel">
            <ArrowLeft size={16} weight="bold" />
          </button>
          <h1 className="text-h3 font-bold text-ink">Costos y desempeño</h1>
          <div className="ml-auto flex items-center gap-1 rounded-[10px] border border-line p-0.5">
            {RANGOS.map((x) => (
              <button key={x.id} onClick={() => setRango(x)}
                className={`rounded-[8px] px-3 py-1 text-meta font-semibold ${x.id === rango.id ? 'bg-sunken text-ink' : 'text-ink-muted hover:text-ink'}`}>
                {x.etiqueta}
              </button>
            ))}
          </div>
          <button className="btn btn-ghost" onClick={onSalir}><SignOut size={16} weight="bold" /> Salir</button>
        </div>
        <p className="mt-2 max-w-3xl text-meta text-ink-muted">
          El modelo local ({MODELO_LOCAL}) corre en esta máquina y no cobra: de él se miden tokens, llamadas y tiempo.
          Lo que cuesta dinero es Claude, que verifica cada respuesta antes de mostrarla.
        </p>

        {error && <p className="mt-4 text-meta text-[var(--red-11)]">{error}</p>}

        <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Kpi titulo="Gasto en Claude" valor={dolares(r?.costo_claude)}
            detalle={`${dolares(r && r.conversaciones ? r.costo_claude / r.conversaciones : null)} por conversación · ${dolares(r && r.preguntas ? r.costo_claude / r.preguntas : null)} por pregunta`} />
          <Kpi titulo="Llamadas a Claude" valor={numero(r && llamadasClaude(r))}
            detalle={`${numero(r?.correcciones)} por correcciones · ${numero(r && r.claude_tokens_entrada + r.claude_tokens_salida)} tokens`} />
          <Kpi titulo="Tokens del modelo local" valor={numero(r && r.tokens_entrada + r.tokens_salida)}
            detalle={`${numero(r?.tokens_entrada)} entrada · ${numero(r?.tokens_salida)} salida · ${numero(r?.llamadas_llm)} llamadas`} />
          <Kpi titulo="Tiempo por pregunta" valor={tiempo(r?.segundos_prom)}
            detalle={`${numero(r?.preguntas)} preguntas · ${pct(r?.desde_cache, r?.preguntas)} desde caché`} />
        </div>

        <div className="card mt-3 overflow-hidden">
          <div className="flex flex-wrap items-center gap-3 border-b border-line-subtle px-4 py-3">
            <h2 className="text-body font-bold text-ink">Por conversación</h2>
            <span className="text-micro text-ink-faint">{numero(r?.conversaciones)} en el periodo</span>
            <select className="campo ml-auto w-auto py-1.5 text-meta" value={orden} aria-label="Ordenar"
              onChange={(e) => setOrden(e.target.value as typeof orden)}>
              {ORDENES.map((o) => <option key={o.id} value={o.id}>{o.etiqueta}</option>)}
            </select>
          </div>
          <div className={`${COLUMNAS} border-b border-line-subtle px-4 py-2 text-micro font-semibold tracking-wide text-ink-faint uppercase`}>
            <span>Conversación</span>
            <span className="hidden text-right sm:block">Modelo local</span>
            <span className="text-right">Claude</span>
            <span className="hidden text-right sm:block">Tiempo</span>
            <span className="hidden sm:block">Veredictos</span>
            <span />
          </div>
          {filas.length === 0 ? (
            <p className="px-4 py-10 text-center text-meta text-ink-faint">Sin conversaciones en este rango.</p>
          ) : (
            <ul className="divide-y divide-[var(--mauve-4)]">
              {filas.map((f) => <FilaConversacion key={f.id} f={f} token={token} onError={manejarError} />)}
            </ul>
          )}
          {hayMas && (
            <div className="border-t border-line-subtle p-3 text-center">
              <button className="btn" onClick={cargarMas}>Cargar más</button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function FilaConversacion({ f, token, onError }: { f: CostoConversacion; token: string; onError: (e: unknown) => void }) {
  const [abierta, setAbierta] = useState(false)
  const [preguntas, setPreguntas] = useState<CostoPregunta[] | null>(null)

  function alternar() {
    setAbierta((a) => !a)
    if (!preguntas) admin<CostoPregunta[]>(`costos/conversaciones/${f.id}`, token, {}).then(setPreguntas).catch(onError)
  }

  return (
    <li>
      <button className={`${COLUMNAS} w-full px-4 py-3 text-left hover:bg-raised`} onClick={alternar} aria-expanded={abierta}>
        <span className="min-w-0">
          <span className="block truncate text-meta font-semibold text-ink" title={f.titulo}>{f.titulo}</span>
          <span className="block text-micro text-ink-faint">
            {fecha(f.ultima)} · {f.preguntas} {f.preguntas === 1 ? 'pregunta' : 'preguntas'}
            {f.desde_cache > 0 && ` · ${f.desde_cache} de caché`}
          </span>
        </span>
        <span className="hidden text-right tabular-nums sm:block">
          <span className="block text-meta text-ink">{numero(f.tokens_entrada)} → {numero(f.tokens_salida)}</span>
          <span className="block text-micro text-ink-faint">{numero(f.llamadas_llm)} llamadas</span>
        </span>
        <span className="text-right tabular-nums">
          <span className="block text-meta font-semibold text-ink">{dolares(f.costo_claude)}</span>
          <span className="block text-micro text-ink-faint">{numero(llamadasClaude(f))} llamadas</span>
        </span>
        <span className="hidden text-right text-meta text-ink tabular-nums sm:block">{tiempo(f.segundos_total)}</span>
        <span className="hidden flex-wrap gap-1 sm:flex">
          <Conteo n={f.aprobadas} tono="ok" titulo="aprobadas" />
          <Conteo n={f.con_observaciones} tono="warn" titulo="con observaciones" />
          <Conteo n={f.reprobadas} tono="bad" titulo="reprobadas" />
        </span>
        <CaretDown size={12} weight="bold" className={`text-ink-faint transition-transform ${abierta ? 'rotate-180' : ''}`} />
      </button>
      {abierta && (
        <div className="bg-raised px-4 pt-1 pb-3">
          {!preguntas ? <p className="py-2 text-micro text-ink-faint">Cargando…</p> : (
            <ol className="m-0 flex list-none flex-col gap-1.5 p-0">
              {preguntas.map((p, i) => <FilaPregunta key={p.id} p={p} n={i + 1} />)}
            </ol>
          )}
        </div>
      )}
    </li>
  )
}

/** Una pregunta de la conversación con lo que costó responderla. */
function FilaPregunta({ p, n }: { p: CostoPregunta; n: number }) {
  const v = p.veredicto ? VEREDICTOS[p.veredicto] : null
  return (
    <li className="rounded-[10px] border border-line-subtle bg-canvas px-3 py-2">
      <div className="flex items-start gap-2">
        <span className="text-micro text-ink-faint tabular-nums">{n}.</span>
        <span className="min-w-0 flex-1 text-meta text-ink">{p.pregunta}</span>
        <span className="shrink-0 text-micro text-ink-faint">{fecha(p.creado_en)}</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 pl-5 text-micro text-ink-muted tabular-nums">
        {p.desde_cache ? (
          <span className="chip" data-tone="ok"><Lightning size={11} weight="fill" /> Caché · {tiempo(p.segundos_total)}</span>
        ) : (
          <span title={p.modelo}>
            <b className="font-semibold text-ink-faint">Local</b>{' '}
            {`${numero(p.tokens_entrada)} → ${numero(p.tokens_salida)} tokens · ${p.llamadas_llm} llamadas · ${tiempo(p.segundos_total)}`}
          </span>
        )}
        {p.costo_claude != null && !p.desde_cache && (
          <span title={p.modelo_auditor ?? ''}>
            <b className="font-semibold text-ink-faint">Claude</b>{' '}
            {`${dolares(p.costo_claude)} · ${numero(p.claude_tokens_entrada)} → ${numero(p.claude_tokens_salida)} tokens`}
            {` · ${p.correcciones + 1} ${p.correcciones > 0 ? 'revisiones' : 'revisión'} · ${tiempo(p.segundos_claude)}`}
          </span>
        )}
        {v && <span className="chip" data-tone={v.tono}>{v.texto}</span>}
        {p.correcciones > 0 && !p.desde_cache && <span className="chip" data-tone="warn">Corregida</span>}
        {p.descartada
          ? <span className="chip" data-tone="bad">Descartada</span>
          : !p.se_puede_responder && <span className="chip">Fuera del artículo</span>}
        {p.error && <span className="chip" data-tone="bad">Error</span>}
      </div>
    </li>
  )
}

function Conteo({ n, tono, titulo }: { n: number; tono: string; titulo: string }) {
  if (!n) return null
  return <span className="chip tabular-nums" data-tone={tono} title={`${n} ${titulo}`}>{n}</span>
}
