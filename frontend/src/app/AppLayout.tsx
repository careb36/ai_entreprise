import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { setOnUnauthorized } from '../services/api'
import { NavBar } from '../components/shell/NavBar'
import { SupportBot } from '../components/SupportBot'
import { ErrorBoundary } from '../components/ErrorBoundary'
import { ToastContainer } from '../components/Toast'
import { AppRoutes } from './AppRoutes'

// ── App shell ─────────────────────────────────────────────────────────────────

/**
 * The page around every route: the navigation bar, the routes inside an error
 * boundary, the support bot and the toasts. It also sends an expired session
 * to the login page and follows the desktop app's native menu.
 */
export function AppLayout() {
  const navigate = useNavigate()

  useEffect(() => {
    setOnUnauthorized(() => {
      if (window.location.pathname === '/login') return
      const from = `${window.location.pathname}${window.location.search}${window.location.hash}`
      navigate(`/login?from=${encodeURIComponent(from)}`, { replace: true })
    })
    return () => setOnUnauthorized(null)
  }, [navigate])

  // Desktop app: the native "Factory" menu asks the renderer to navigate.
  useEffect(() => {
    const onNavigate = window.electronAPI?.onNavigate
    if (!onNavigate) return
    return onNavigate(path => {
      if (path.startsWith('/')) navigate(path)
    })
  }, [navigate])

  return (
    <div className="min-h-screen bg-surface-950 flex flex-col relative">
      <NavBar />
      <main className="flex-1 overflow-auto">
        <ErrorBoundary>
          <AppRoutes />
        </ErrorBoundary>
      </main>
      <SupportBot />
      <ToastContainer />
    </div>
  )
}
