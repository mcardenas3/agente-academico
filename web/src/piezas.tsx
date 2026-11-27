// Piezas que comparten el panel /admin y la vista de costos

/** Cómo se muestra cada veredicto de Claude: texto y tono (colores de index.css). */
export const VEREDICTOS = {
  aprobada: { texto: 'Aprobada', tono: 'ok' },
  con_observaciones: { texto: 'Con observaciones', tono: 'warn' },
  reprobada: { texto: 'Reprobada', tono: 'bad' },
} as const

/** Tarjeta de un indicador: título, valor grande y una línea de detalle. */
export function Kpi({ titulo, valor, detalle }: { titulo: string; valor: string; detalle: string }) {
  return (
    <div className="card px-4 py-3.5">
      <div className="text-micro font-semibold tracking-wide text-ink-faint uppercase">{titulo}</div>
      <div className="mt-1.5 text-kpi font-bold text-ink tabular-nums">{valor}</div>
      <div className="mt-1 truncate text-micro text-ink-muted" title={detalle}>{detalle}</div>
    </div>
  )
}
