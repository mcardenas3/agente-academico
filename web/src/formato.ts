// Formatos de números, tiempos y citas para que se lean igual en toda la interfaz

const entero = new Intl.NumberFormat('es-MX', { maximumFractionDigits: 0 })
const decimal = new Intl.NumberFormat('es-MX', { maximumFractionDigits: 1, minimumFractionDigits: 1 })

export const numero = (n: number | null | undefined) => (n == null ? '—' : entero.format(n))

/** 0.002 → "2 ms"; 5.93 → "5.9 s" */
export function tiempo(segundos: number | null | undefined) {
  if (segundos == null) return '—'
  return segundos < 1 ? `${entero.format(segundos * 1000)} ms` : `${decimal.format(segundos)} s`
}

/** "5 Post-Retrieval > 5.1 Re-Ranking" → "5.1 Re-Ranking" (la ruta completa va en el title) */
export const ultimaSeccion = (ruta: string) => ruta.split(' > ').at(-1) ?? ruta

/** [16, 17] → "p. 16–17"; [12] → "p. 12" */
export function paginas(lista: number[]) {
  if (lista.length === 0) return ''
  const [a, b] = [Math.min(...lista), Math.max(...lista)]
  return a === b ? `p. ${a}` : `p. ${a}–${b}`
}

export function fecha(iso: string) {
  return new Date(iso).toLocaleString('es-MX', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

/** Porcentaje entero de una parte sobre un total ("—" si no hay total). */
export function pct(parte: number | null | undefined, total: number | null | undefined): string {
  return total ? `${Math.round((100 * (parte ?? 0)) / total)} %` : '—'
}

/** Dólares con los decimales que hagan falta: las llamadas a Claude cuestan fracciones de centavo. */
export function dolares(v: number | null | undefined): string {
  if (v == null) return '—'
  return `US$${v < 1 ? v.toFixed(4) : v.toFixed(2)}`
}

/** Rangos de fechas de los paneles de /admin */
export const RANGOS = [
  { id: 'hoy', etiqueta: 'Hoy', dias: 0 },
  { id: '7', etiqueta: '7 días', dias: 7 },
  { id: '30', etiqueta: '30 días', dias: 30 },
  { id: 'todo', etiqueta: 'Todo', dias: null },
] as const

export type Rango = (typeof RANGOS)[number]

/** Inicio del rango (medianoche de hace N días), o undefined para "Todo". */
export function desdeDe(rango: Rango): string | undefined {
  if (rango.dias === null) return undefined
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - rango.dias)
  return d.toISOString()
}
