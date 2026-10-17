import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from './store/useAppStore'
import { useAuthStore } from './store/useAuthStore'
import { setOnUnauthorized } from './services/api'
import ProtectedRoute from './components/ProtectedRoute'
import Dashboard from './pages/Dashboard'
import Home from './pages/Home'
import CompanyNew from './pages/CompanyNew'
import CompanySelect from './pages/CompanySelect'
import Login from './pages/Login'
import Register from './pages/Register'
import { SprintWorkspace } from './pages/SprintWorkspace'
import { GovernancePanel } from './components/GovernancePanel'
import { AnalyticsDashboard } from './components/AnalyticsDashboard'
import { SupportBot } from './components/SupportBot'
import MemorySearch from './pages/MemorySearch'
import Evidence from './pages/Evidence'
import EvidenceDetail from './pages/EvidenceDetail'
import Generate from './pages/Generate'
import Glassroom from './pages/Glassroom'
import NotFound from './pages/NotFound'
import TaskDetails from './pages/TaskDetails'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ToastContainer } from './components/Toast'
import {
  LayoutDashboard, ShieldCheck, PieChart, MonitorPlay, ClipboardCheck,
  Globe, Database, Code2, Menu, X, Building2
} from 'lucide-react'
import { STORAGE_KEY_LANG } from './lib/constants'

// ── Language Switcher ─────────────────────────────────────────────────────────

export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()
  const current = (i18n.resolvedLanguage || i18n.language).split('-')[0] === 'es' ? 'es' : 'en'
  const next = current === 'en' ? 'es' : 'en'

  function toggle() {
    i18n.changeLanguage(next)
    localStorage.setItem(STORAGE_KEY_LANG, next)
  }

  return (
    <button
      onClick={toggle}
      aria-label={t(`language.switchTo.${next}`)}
      className="btn-ghost btn-sm flex items-center gap-1.5 border border-surface-700"
    >
      <Globe size={14} />
      {current.toUpperCase()}
    </button>
  )
}

// ── Nav links config ──────────────────────────────────────────────────────────

function useNavLinks() {
  const { t } = useTranslation()
  const activeSprintId = useAppStore(state => state.activeSprintId)
   const workspaceTo = activeSprintId ? `/workspace/${activeSprintId}` : '/evidence'
  return [
     { to: '/',           icon: LayoutDashboard, label: t('nav.home') },
     { to: '/companies/new', icon: Building2, label: t('nav.newCompany') },
     { to: '/dashboard',  icon: LayoutDashboard, label: t('nav.dashboard') },
     { to: workspaceTo,   icon: MonitorPlay, label: t('nav.workspace') },
     { to: '/governance', icon: ShieldCheck,    label: t('nav.governance') },
     { to: '/evidence', icon: ClipboardCheck, label: t('nav.evidence') },
    { to: '/analytics',  icon: PieChart,       label: t('nav.analytics') },
    { to: '/memory',     icon: Database,       label: t('nav.memory') },
    { to: '/generate',   icon: Code2,          label: t('nav.generate') },
  ]
}

// ── NavBar ────────────────────────────────────────────────────────────────────

function NavBar() {
  const [menuOpen, setMenuOpen] = useState(false)
  const { t } = useTranslation()
  const links = useNavLinks()
  const navigate = useNavigate()
  const user = useAuthStore(state => state.user)
  const token = useAuthStore(state => state.token)
  const expiresAt = useAuthStore(state => state.expiresAt)
  const logout = useAuthStore(state => state.logout)
  const authenticated = Boolean(token && expiresAt && expiresAt > Date.now())

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-1.5 text-sm font-medium px-3 py-2 rounded-lg transition-colors
     ${isActive
       ? 'text-white bg-brand-600/20 border border-brand-600/30'
       : 'text-gray-400 hover:text-white hover:bg-surface-800'}`

  return (
    <header className="h-14 bg-surface-900 border-b border-surface-700 text-white flex items-center px-4 lg:px-6 z-20 flex-shrink-0">
      {/* Logo */}
      <Link to="/" className="flex items-center gap-2.5 hover:opacity-80 transition-opacity flex-shrink-0">
        <img src="/fabrique-icon.svg" alt="La Fabrique" width={30} height={30} className="rounded-md" />
        <span className="text-lg font-black tracking-tight hidden xs:block">La Fabrique</span>
      </Link>

      {/* Desktop nav */}
      {authenticated && (
        <nav className="hidden lg:flex items-center gap-1 ml-8" aria-label="Main navigation">
          {links.map(({ to, icon: Icon, label }) => (
            <NavLink key={to} to={to} end={to === '/'} className={linkClass}>
              <Icon size={16} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
      )}

      {/* Right side */}
      <div className="ml-auto flex items-center gap-2">
        <LanguageSwitcher />
        {authenticated && (
          <>
            <span className="hidden sm:inline text-sm text-gray-400">
              {user?.full_name || user?.email}
            </span>
            <button
              type="button"
              className="btn-ghost btn-sm"
              onClick={() => {
                logout()
                navigate('/login', { replace: true })
              }}
            >
              {t('auth.logout', { defaultValue: 'Log out' })}
            </button>
          </>
        )}

        {/* Status pill — desktop only */}
        <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 bg-surface-800 rounded-full border border-surface-700 text-xs text-gray-400">
          <span className="status-dot-pulse" />
          Online
        </div>

        {/* Hamburger — mobile/tablet */}
        {authenticated && <button
          aria-label={menuOpen ? 'Close menu' : 'Open menu'}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen(v => !v)}
          className="btn-ghost btn-icon lg:hidden"
        >
          {menuOpen ? <X size={20} /> : <Menu size={20} />}
        </button>}
      </div>

      {/* Mobile dropdown */}
      {authenticated && menuOpen && (
        <div
          className="absolute top-14 left-0 right-0 bg-surface-900 border-b border-surface-700 lg:hidden z-30 animate-fade-in"
          onClick={() => setMenuOpen(false)}
        >
          <nav className="flex flex-col p-3 gap-1" aria-label="Mobile navigation">
            {links.map(({ to, icon: Icon, label }) => (
              <NavLink key={to} to={to} end={to === '/'} className={linkClass}>
                <Icon size={16} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
        </div>
      )}
    </header>
  )
}

// ── App shell ─────────────────────────────────────────────────────────────────

function AppContent() {
  const activeSprintId = useAppStore(state => state.activeSprintId)
  const navigate = useNavigate()

  useEffect(() => {
    setOnUnauthorized(() => {
      if (window.location.pathname === '/login') return
      const from = `${window.location.pathname}${window.location.search}${window.location.hash}`
      navigate(`/login?from=${encodeURIComponent(from)}`, { replace: true })
    })
    return () => setOnUnauthorized(null)
  }, [navigate])

  return (
    <div className="min-h-screen bg-surface-950 flex flex-col relative">
      <NavBar />
      <main className="flex-1 overflow-auto">
        <ErrorBoundary>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/register" element={<Register />} />
            <Route element={<ProtectedRoute />}>
              <Route path="/" element={<Home />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/companies/new" element={<CompanyNew />} />
              <Route path="/companies/select" element={<CompanySelect />} />
              <Route path="/workspace/:id" element={<SprintWorkspace />} />
              <Route
                path="/governance"
                element={<div className="page-container"><GovernancePanel /></div>}
              />
              <Route path="/evidence" element={<Evidence />} />
              <Route path="/evidence/:runId" element={<EvidenceDetail />} />
              <Route
                path="/analytics"
                element={<div className="page-container"><AnalyticsDashboard sprintId={activeSprintId ?? 0} /></div>}
              />
              <Route path="/memory" element={<MemorySearch />} />
              <Route path="/generate" element={<Generate />} />
              <Route path="/glassroom" element={<Glassroom />} />
              <Route path="/workspace/:sprintId/task/:taskId" element={<TaskDetails />} />
              <Route path="/task/:taskId" element={<TaskDetails />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </ErrorBoundary>
      </main>
      <SupportBot />
      <ToastContainer />
    </div>
  )
}

function App() {
  return (
    <BrowserRouter>
      <AppContent />
    </BrowserRouter>
  )
}

export default App
