import type { ConversacionResumen } from './api'
import { NotePencil, Trash, X } from './iconos'

type Props = {
  conversaciones: ConversacionResumen[]
  activa: string | null
  ocupado: boolean
  abierto: boolean            // en celular, la barra se abre encima del chat
  onCerrar: () => void
  onNueva: () => void
  onAbrir: (id: string) => void
  onOcultar: (id: string) => void
}

/** "hoy 14:05", "ayer", "12 nov": cuándo se usó por última vez. */
function cuando(iso: string) {
  const fecha = new Date(iso)
  const hoy = new Date()
  const dias = Math.round((new Date(hoy.toDateString()).getTime() - new Date(fecha.toDateString()).getTime()) / 864e5)
  if (dias === 0) return fecha.toLocaleTimeString('es-MX', { hour: '2-digit', minute: '2-digit' })
  if (dias === 1) return 'ayer'
  return fecha.toLocaleDateString('es-MX', { day: 'numeric', month: 'short' })
}

/** Barra lateral con las conversaciones de este navegador. */
export default function Historial(p: Props) {
  const contenido = (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 p-3">
        <button className="btn flex-1 justify-center" onClick={p.onNueva} disabled={p.ocupado}>
          <NotePencil size={15} weight="bold" /> Nueva conversación
        </button>
        <button className="btn btn-ghost px-2 md:hidden" onClick={p.onCerrar} aria-label="Cerrar historial">
          <X size={16} weight="bold" />
        </button>
      </div>
      <div className="px-4 pb-1.5 text-micro font-semibold tracking-wide text-ink-faint uppercase">Historial</div>
      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {p.conversaciones.length === 0 ? (
          <p className="px-2 py-3 text-micro text-ink-faint">Tus conversaciones aparecerán aquí.</p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {p.conversaciones.map((c) => {
              const activa = c.id === p.activa
              return (
                <li key={c.id} className="group relative">
                  <button
                    onClick={() => p.onAbrir(c.id)}
                    disabled={p.ocupado}
                    aria-current={activa ? 'true' : undefined}
                    className={`w-full rounded-[9px] py-2 pr-8 pl-2.5 text-left transition-colors disabled:opacity-60 ${activa
                      ? 'bg-brand-surface text-ink' : 'text-ink-muted hover:bg-hover hover:text-ink'}`}>
                    <span className="block truncate text-meta font-medium">{c.titulo ?? 'Conversación'}</span>
                    <span className="block text-micro text-ink-faint">
                      {cuando(c.actualizada_en)} · {c.preguntas} {c.preguntas === 1 ? 'pregunta' : 'preguntas'}
                    </span>
                  </button>
                  <button
                    onClick={() => p.onOcultar(c.id)}
                    disabled={p.ocupado}
                    className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded-[7px] p-1.5 text-ink-faint opacity-0 transition-opacity group-hover:opacity-100 hover:bg-[var(--red-3)] hover:text-[var(--red-11)] focus:opacity-100"
                    aria-label="Quitar del historial" title="Quitar del historial">
                    <Trash size={14} weight="bold" />
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </nav>
    </div>
  )

  return (
    <>
      {/* Escritorio: columna fija a la izquierda */}
      <aside className="hidden w-64 shrink-0 border-r border-line-subtle bg-raised md:block">{contenido}</aside>
      {/* Celular: encima del chat, con fondo oscuro para cerrarla tocando afuera */}
      {p.abierto && (
        <div className="fixed inset-0 z-30 md:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={p.onCerrar} />
          <aside className="absolute inset-y-0 left-0 w-72 max-w-[85%] border-r border-line-subtle bg-raised">{contenido}</aside>
        </div>
      )}
    </>
  )
}
