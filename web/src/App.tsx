import { lazy, Suspense, useEffect, useState } from 'react'
import Chat from './Chat'

// El panel trae la librería de gráficas: se descarga solo al entrar a /admin
const Admin = lazy(() => import('./Admin'))
import { BookOpenText, ChartBar, ChatCircleText, Moon, Sun } from './iconos'

/** Tema claro u oscuro: la clase .dark en <html> cambia todas las escalas de color. */
function useTema() {
  const [oscuro, setOscuro] = useState(() => document.documentElement.classList.contains('dark'))
  useEffect(() => {
    document.documentElement.classList.toggle('dark', oscuro)
    try { localStorage.setItem('tema', oscuro ? 'oscuro' : 'claro') } catch { /* sin almacenamiento */ }
  }, [oscuro])
  return [oscuro, () => setOscuro((o) => !o)] as const
}

export default function App() {
  const [oscuro, cambiarTema] = useTema()
  // Solo dos pantallas, así que basta mirar la ruta: /admin o el chat
  const enAdmin = window.location.pathname.startsWith('/admin')

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line-subtle px-4 sm:px-6">
        <a href="/" className="flex items-center gap-2.5 no-underline">
          <span className="grid size-8 place-items-center rounded-[9px] bg-brand text-white">
            <BookOpenText size={18} weight="bold" />
          </span>
          <span className="leading-tight">
            <span className="block text-[14px] font-bold text-ink">Agente académico</span>
            <span className="hidden text-micro text-ink-faint sm:block">
              Huang &amp; Huang · ACM Computing Surveys, 2026
            </span>
          </span>
        </a>
        <nav className="ml-auto flex items-center gap-1">
          {enAdmin ? (
            <a href="/" className="btn btn-ghost"><ChatCircleText size={16} weight="bold" /> Chat</a>
          ) : (
            <a href="/admin" className="btn btn-ghost"><ChartBar size={16} weight="bold" /> Panel</a>
          )}
          <button className="btn btn-ghost" onClick={cambiarTema} aria-label="Cambiar tema" title="Cambiar tema">
            {oscuro ? <Sun size={16} weight="bold" /> : <Moon size={16} weight="bold" />}
          </button>
        </nav>
      </header>
      <main className="min-h-0 flex-1">
        {enAdmin ? <Suspense fallback={null}><Admin oscuro={oscuro} /></Suspense> : <Chat />}
      </main>
    </div>
  )
}
