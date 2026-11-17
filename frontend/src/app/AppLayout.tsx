import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
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
  const { t } = useTranslation()

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
    // The bar stays in view and the page scrolls inside <main>, so a page's
    // sticky toolbar (components/ui/Toolbar) sticks right under the bar.
    <div className="relative flex h-dvh flex-col bg-surface-950">
      <a href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-2 focus:z-50 focus:rounded-md focus:bg-surface-800 focus:px-3 focus:py-1.5 focus:text-[13px] focus:text-white focus:ring-2 focus:ring-brand-500">
        {t('nav.skipToContent')}
      </a>
      <NavBar />
      <main id="main-content" tabIndex={-1} className="min-h-0 flex-1 overflow-auto focus:outline-none">
        <ErrorBoundary>
          <AppRoutes />
        </ErrorBoundary>
      </main>
      <SupportBot />
      <ToastContainer />
    </div>
  )
}
