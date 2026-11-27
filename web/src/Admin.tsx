import { useCallback, useEffect, useMemo, useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { admin, NoAutorizado, type Dia, type FilaPregunta, type Resumen } from './api'
import { fecha, numero, tiempo } from './formato'
import { Lightning, LockSimple, MagnifyingGlass, ShieldCheck, ShieldWarning, SignOut } from './iconos'

const VEREDICTOS = {
  aprobada: { texto: 'Aprobada', tono: 'ok' },
  con_observaciones: { texto: 'Con observaciones', tono: 'warn' },
  reprobada: { texto: 'Reprobada', tono: 'bad' },
} as const

/** Porcentaje entero de una parte sobre un total ("—" si no hay total). */
function pct(parte: number | undefined, total: number | undefined): string {
  return total ? `${Math.round((100 * (parte ?? 0)) / total)} %` : '—'
}

/** Dólares con los decimales que hagan falta: las auditorías cuestan fracciones de centavo. */
function dolares(v: number | undefined): string {
  if (v === undefined) return '—'
  return `US$${v < 1 ? v.toFixed(4) : v.toFixed(2)}`
}

const LLAVE_TOKEN = 'admin_token'
const POR_PAGINA = 25
const RANGOS = [
  { id: 'hoy', etiqueta: 'Hoy', dias: 0 },
  { id: '7', etiqueta: '7 días', dias: 7 },
  { id: '30', etiqueta: '30 días', dias: 30 },
  { id: 'todo', etiqueta: 'Todo', dias: null },
] as const

function desdeDe(rango: (typeof RANGOS)[number]): string | undefined {
  if (rango.dias === null) return undefined
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - rango.dias)
  return d.toISOString()
}

export default function Admin({ oscuro }: { oscuro: boolean }) {
  const [token, setToken] = useState<string | null>(() => {
    try { return sessionStorage.getItem(LLAVE_TOKEN) } catch { return null }
  })
  if (!token) return <Acceso onEntrar={setToken} />
  return <Panel token={token} oscuro={oscuro} onSalir={() => {
    try { sessionStorage.removeItem(LLAVE_TOKEN) } catch { /* nada */ }
    setToken(null)
  }} />
}

function Acceso({ onEntrar }: { onEntrar: (t: string) => void }) {
  const [valor, setValor] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [probando, setProbando] = useState(false)

  async function entrar(e: React.FormEvent) {
    e.preventDefault()
    setProbando(true)
    setError(null)
    try {
      await admin('resumen', valor.trim(), {})  // si el token no sirve, responde 401
      try { sessionStorage.setItem(LLAVE_TOKEN, valor.trim()) } catch { /* nada */ }
      onEntrar(valor.trim())
    } catch (err) {
      setError(err instanceof NoAutorizado ? err.message : 'No se pudo conectar con el servidor.')
    }
    setProbando(false)
  }

  return (
    <div className="grid h-full place-items-center px-4">
      <form onSubmit={entrar} className="card w-full max-w-sm p-6">
        <span className="grid size-9 place-items-center rounded-[10px] bg-sunken text-ink-muted">
          <LockSimple size={18} weight="bold" />
        </span>
        <h1 className="mt-4 text-h3 font-bold text-ink">Panel de estadísticas</h1>
        <p className="mt-1 text-meta text-ink-muted">Escribe el token de acceso (ADMIN_TOKEN del archivo .env).</p>
        <input type="password" className="campo mt-4" placeholder="Token" value={valor} autoFocus
          onChange={(e) => setValor(e.target.value)} />
        {error && <p className="mt-2 text-meta text-[var(--red-11)]">{error}</p>}
        <button className="btn btn-primary mt-4 w-full justify-center" disabled={!valor.trim() || probando}>
          {probando ? 'Revisando…' : 'Entrar'}
        </button>
      </form>
    </div>
  )
}

function Panel({ token, oscuro, onSalir }: { token: string; oscuro: boolean; onSalir: () => void }) {
  const [rango, setRango] = useState<(typeof RANGOS)[number]>(RANGOS[3])
  const [busqueda, setBusqueda] = useState('')
  const [texto, setTexto] = useState('')  // lo buscado, con un respiro para no consultar en cada tecla
  const [resumen, setResumen] = useState<Resumen | null>(null)
  const [dias, setDias] = useState<Dia[]>([])
  const [filas, setFilas] = useState<FilaPregunta[]>([])
  const [hayMas, setHayMas] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { const t = setTimeout(() => setTexto(busqueda), 300); return () => clearTimeout(t) }, [busqueda])

  const desde = desdeDe(rango)
  const manejarError = useCallback((err: unknown) => {
    if (err instanceof NoAutorizado) onSalir()
    else setError('No se pudieron cargar los datos.')
  }, [onSalir])

  useEffect(() => {
    setError(null)
    Promise.all([
      admin<Resumen>('resumen', token, { desde }),
      admin<Dia[]>('por-dia', token, { desde }),
    ]).then(([r, d]) => { setResumen(r); setDias(d) }).catch(manejarError)
  }, [token, desde, manejarError])

  useEffect(() => {
    admin<FilaPregunta[]>('preguntas', token, { desde, texto, limite: POR_PAGINA })
      .then((f) => { setFilas(f); setHayMas(f.length === POR_PAGINA) }).catch(manejarError)
  }, [token, desde, texto, manejarError])

  function cargarMas() {
    admin<FilaPregunta[]>('preguntas', token, { desde, texto, limite: POR_PAGINA, desplazamiento: filas.length })
      .then((f) => { setFilas((a) => [...a, ...f]); setHayMas(f.length === POR_PAGINA) }).catch(manejarError)
  }

  const r = resumen
  const a = r?.auditoria
  const porcentajeCache = r && r.preguntas ? Math.round((100 * r.desde_cache) / r.preguntas) : 0

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-h3 font-bold text-ink">Estadísticas</h1>
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

        {error && <p className="mt-4 text-meta text-[var(--red-11)]">{error}</p>}

        <div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Kpi titulo="Preguntas" valor={numero(r?.preguntas)} detalle={`${numero(r?.conversaciones)} conversaciones`} />
          <Kpi titulo="Tiempo promedio" valor={tiempo(r?.segundos_prom_sin_cache)}
            detalle={`búsqueda ${tiempo(r?.segundos_busqueda_prom)} · LLM ${tiempo(r?.segundos_llm_prom)}`} />
          <Kpi titulo="Tokens de entrada" valor={numero(r?.tokens_entrada)} detalle={`${numero(r?.tokens_entrada_prom)} por pregunta`} />
          <Kpi titulo="Tokens de salida" valor={numero(r?.tokens_salida)} detalle={`${numero(r?.tokens_salida_prom)} por pregunta`} />
          <Kpi titulo="Desde caché" valor={`${porcentajeCache} %`}
            detalle={`${tiempo(r?.segundos_prom_cache)} contra ${tiempo(r?.segundos_prom_sin_cache)}`} />
          <Kpi titulo="Fuera del artículo" valor={numero(r?.sin_respuesta)} detalle="preguntas que no cubre" />
          <Kpi titulo="Errores" valor={numero(r?.errores)} detalle="sin respuesta válida del LLM" />
          <Kpi titulo="Tiempo con caché" valor={tiempo(r?.segundos_prom)} detalle="promedio de todas" />
        </div>

        <div className="mt-6 flex items-baseline gap-2">
          <h2 className="text-body font-bold text-ink">Auditoría con Claude</h2>
          <span className="text-micro text-ink-faint">cada respuesta revisada contra los fragmentos que recuperó la búsqueda</span>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Kpi titulo="Aprobadas" valor={pct(a?.aprobadas, a?.auditadas)}
            detalle={`${numero(a?.con_observaciones)} con observaciones · ${numero(a?.reprobadas)} reprobadas`} />
          <Kpi titulo="Fieles al artículo" valor={pct(a?.fieles, a?.auditadas)}
            detalle={`citas correctas ${pct(a?.citas_correctas, a?.auditadas)}`} />
          <Kpi titulo="Relevancia" valor={a?.relevancia_prom != null ? `${a.relevancia_prom.toFixed(1)} / 5` : '—'}
            detalle={`acertó en responder o abstenerse ${pct(a?.abstencion_correcta, a?.auditadas)}`} />
          <Kpi titulo="Costo de Claude" valor={dolares(a?.costo_usd)}
            detalle={`${numero(a?.auditadas)} auditadas · ${tiempo(a?.segundos_prom)} cada una`} />
        </div>

        <div className="card mt-3 p-4">
          <div className="mb-3 flex items-baseline gap-2">
            <h2 className="text-body font-bold text-ink">Preguntas por día</h2>
            <span className="text-micro text-ink-faint">barras: preguntas · línea: segundos promedio</span>
          </div>
          <Grafica dias={dias} oscuro={oscuro} />
        </div>

        <div className="card mt-3 overflow-hidden">
          <div className="flex items-center gap-3 border-b border-line-subtle px-4 py-3">
            <h2 className="text-body font-bold text-ink">Conversaciones</h2>
            <label className="relative ml-auto w-full max-w-xs">
              <MagnifyingGlass size={14} className="absolute top-1/2 left-3 -translate-y-1/2 text-ink-faint" />
              <input className="campo py-1.5 pl-8 text-meta" placeholder="Buscar en preguntas y respuestas"
                value={busqueda} onChange={(e) => setBusqueda(e.target.value)} />
            </label>
          </div>
          {filas.length === 0 ? (
            <p className="px-4 py-10 text-center text-meta text-ink-faint">Sin preguntas en este rango.</p>
          ) : (
            <ul className="divide-y divide-[var(--mauve-4)]">
              {filas.map((f) => <FilaDetalle key={f.id} f={f} />)}
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

function Kpi({ titulo, valor, detalle }: { titulo: string; valor: string; detalle: string }) {
  return (
    <div className="card px-4 py-3.5">
      <div className="text-micro font-semibold tracking-wide text-ink-faint uppercase">{titulo}</div>
      <div className="mt-1.5 text-kpi font-bold text-ink tabular-nums">{valor}</div>
      <div className="mt-1 truncate text-micro text-ink-muted">{detalle}</div>
    </div>
  )
}

function FilaDetalle({ f }: { f: FilaPregunta }) {
  const [abierta, setAbierta] = useState(false)
  return (
    <li>
      <button className="w-full px-4 py-3 text-left hover:bg-raised" onClick={() => setAbierta((a) => !a)}>
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-body font-semibold text-ink">{f.pregunta}</div>
            <div className={`mt-0.5 text-meta text-ink-muted ${abierta ? 'whitespace-pre-wrap' : 'line-clamp-2'}`}>{f.respuesta}</div>
          </div>
          <div className="shrink-0 text-right text-micro text-ink-faint tabular-nums">
            <div>{fecha(f.creado_en)}</div>
            <div className="mt-0.5">{tiempo(f.segundos_total)}</div>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-micro text-ink-faint">
          {f.desde_cache
            ? <span className="chip" data-tone="ok"><Lightning size={11} weight="fill" /> Caché</span>
            : <span className="tabular-nums">{numero(f.tokens_entrada)} → {numero(f.tokens_salida)} tokens</span>}
          {!f.se_puede_responder && <span className="chip" data-tone="warn">Fuera del artículo</span>}
          {f.error && <span className="chip" data-tone="bad">Error</span>}
          {f.auditoria && (
            <span className="chip" data-tone={VEREDICTOS[f.auditoria.veredicto].tono}>
              {f.auditoria.veredicto === 'aprobada' ? <ShieldCheck size={11} weight="bold" /> : <ShieldWarning size={11} weight="bold" />}
              {VEREDICTOS[f.auditoria.veredicto].texto}
            </span>
          )}
        </div>
        {abierta && f.auditoria && (
          <div className="mt-3 rounded-[10px] bg-raised px-3 py-2.5 text-micro">
            <div className="text-ink-faint">Auditoría ({f.auditoria.modelo})</div>
            <p className="m-0 mt-0.5 text-meta text-ink">{f.auditoria.analisis}</p>
            {f.auditoria.problemas.length > 0 && (
              <ul className="m-0 mt-1.5 list-disc pl-5 text-ink-muted">
                {f.auditoria.problemas.map((p) => <li key={p}>{p}</li>)}
              </ul>
            )}
          </div>
        )}
        {abierta && !f.desde_cache && (
          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 rounded-[10px] bg-raised px-3 py-2.5 text-micro sm:grid-cols-4">
            <Dato nombre="Modelo" valor={f.modelo} />
            <Dato nombre="Llamadas al LLM" valor={String(f.llamadas_llm)} />
            <Dato nombre="Búsqueda" valor={tiempo(f.segundos_busqueda)} />
            <Dato nombre="LLM" valor={tiempo(f.segundos_total - f.segundos_busqueda)} />
            <Dato nombre="Tools" valor={f.tools.join(', ') || '—'} />
            <Dato nombre="Buscó (BM25)" valor={f.consulta_en ?? '—'} ancho />
            {f.error && <Dato nombre="Error" valor={f.error} ancho />}
          </dl>
        )}
      </button>
    </li>
  )
}

function Dato({ nombre, valor, ancho }: { nombre: string; valor: string; ancho?: boolean }) {
  return (
    <div className={ancho ? 'col-span-2' : ''}>
      <dt className="text-ink-faint">{nombre}</dt>
      <dd className="text-ink">{valor}</dd>
    </div>
  )
}

/** Recharts escribe los colores como atributos del SVG, donde var(--…) no funciona: se leen los
 * valores reales de las variables CSS (y se vuelven a leer al cambiar el tema). */
function useColores(oscuro: boolean) {
  return useMemo(() => {
    const css = getComputedStyle(document.documentElement)
    const v = (n: string) => css.getPropertyValue(n).trim()
    return { barra: v('--violet-9'), linea: v('--jade-9'), reja: v('--mauve-4'), texto: v('--mauve-10'), fondo: v('--mauve-2'), borde: v('--mauve-6') }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [oscuro])
}

function Grafica({ dias, oscuro }: { dias: Dia[]; oscuro: boolean }) {
  const c = useColores(oscuro)
  const datos = dias.map((d) => ({ ...d, etiqueta: new Date(d.dia + 'T00:00').toLocaleDateString('es-MX', { day: '2-digit', month: 'short' }) }))
  if (datos.length === 0) return <p className="py-10 text-center text-meta text-ink-faint">Sin datos todavía.</p>
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={datos} margin={{ top: 4, right: 4, bottom: 0, left: -18 }}>
          <CartesianGrid stroke={c.reja} vertical={false} />
          <XAxis dataKey="etiqueta" tick={{ fill: c.texto, fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis yAxisId="n" allowDecimals={false} tick={{ fill: c.texto, fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis yAxisId="s" orientation="right" tick={{ fill: c.texto, fontSize: 11 }} axisLine={false} tickLine={false} unit=" s" />
          <Tooltip contentStyle={{ background: c.fondo, border: `1px solid ${c.borde}`, borderRadius: 10, fontSize: 12 }}
            formatter={(v, n) => (n === 'segundos_prom' ? [tiempo(Number(v)), 'Tiempo promedio'] : [v, 'Preguntas'])} />
          <Bar yAxisId="n" dataKey="preguntas" fill={c.barra} radius={[6, 6, 0, 0]} maxBarSize={36} />
          <Line yAxisId="s" dataKey="segundos_prom" stroke={c.linea} strokeWidth={2} dot={{ r: 3, fill: c.linea }} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
