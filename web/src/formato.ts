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
