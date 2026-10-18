import { useEffect, useRef, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Link, Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from './store/useAppStore'
import { useAuthStore } from './store/useAuthStore'
import { setOnUnauthorized } from './services/api'
import ProtectedRoute from './components/ProtectedRoute'
import Dashboard from './pages/Dashboard'
import CompanyNew from './pages/CompanyNew'
import CompanySelect from './pages/CompanySelect'
import Login from './pages/Login'
import Register from './pages/Register'
import { SprintWorkspace } from './pages/SprintWorkspace'
import { GovernancePanel } from './components/GovernancePanel'
import { QuotaPanel } from './components/QuotaPanel'
import { AnalyticsDashboard } from './components/AnalyticsDashboard'
import { SupportBot } from './components/SupportBot'
import MemorySearch from './pages/MemorySearch'
import Evidence from './pages/Evidence'
import EvidenceDetail from './pages/EvidenceDetail'
import Generate from './pages/Generate'
import NotFound from './pages/NotFound'
import TaskDetails from './pages/TaskDetails'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ToastContainer } from './components/Toast'
import {
  LayoutDashboard, ShieldCheck, ClipboardCheck,
  Globe, Database, Code2, Menu, X, Building2, ChevronDown
} from 'lucide-react'
import { STORAGE_KEY_LANG } from './lib/constants'
import { useCapabilities } from './hooks/useQueries'

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

function useNavLinks(authenticated: boolean) {
  const { t } = useTranslation()
  const { data, isError } = useCapabilities(authenticated)
  return [
    { to: '/evidence', icon: ClipboardCheck, label: t('nav.evidence', { defaultValue: 'Evidence' }) },
    { to: '/governance', icon: ShieldCheck, label: t('nav.review', { defaultValue: 'Review' }) },
    { to: '/dashboard', icon: LayoutDashboard, label: t('nav.sprints', { defaultValue: 'Sprints' }) },
    ...(!isError && data?.agent_graph === true ? [{ to: '/generate', icon: Code2, label: t('nav.generate', { defaultValue: 'Generate' }) }] : []),
    ...(!isError && data?.memory === true ? [{ to: '/memory', icon: Database, label: t('nav.memory', { defaultValue: 'Memory' }) }] : []),
  ]
}

// ── NavBar ────────────────────────────────────────────────────────────────────

function NavBar() {
  const [menuOpen, setMenuOpen] = useState(false)
  const [companyOpen, setCompanyOpen] = useState(false)
  const companyMenu = useRef<HTMLDivElement>(null)
  const companyToggle = useRef<HTMLButtonElement>(null)
  const mobileToggle = useRef<HTMLButtonElement>(null)
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const user = useAuthStore(state => state.user)
  const token = useAuthStore(state => state.token)
  const expiresAt = useAuthStore(state => state.expiresAt)
  const logout = useAuthStore(state => state.logout)
  const memberships = useAuthStore(state => state.memberships)
  const companyName = useAppStore(state => state.companyName)
  const companyId = useAppStore(state => state.companyId)
  const authenticated = Boolean(token && expiresAt && expiresAt > Date.now())
  const links = useNavLinks(authenticated)

  useEffect(() => {
    setMenuOpen(false)
    setCompanyOpen(false)
  }, [location])

  useEffect(() => {
    if (!companyOpen) return
    function closeOutside(event: MouseEvent) {
      if (!companyMenu.current?.contains(event.target as Node)) setCompanyOpen(false)
    }
    document.addEventListener('mousedown', closeOutside)
    return () => document.removeEventListener('mousedown', closeOutside)
  }, [companyOpen])

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-1.5 whitespace-nowrap text-sm font-medium px-2 py-2 rounded-lg transition-colors
     ${isActive
       ? 'text-white bg-brand-600/20 border border-brand-600/30'
       : 'text-gray-400 hover:text-white hover:bg-surface-800'}`

  return (
    <header className="relative h-14 bg-surface-900 border-b border-surface-700 text-white flex items-center gap-2 px-4 lg:px-6 z-20 flex-shrink-0" onKeyDown={event => {
      if (event.key === 'Escape' && menuOpen) {
        setMenuOpen(false)
        mobileToggle.current?.focus()
      }
    }}>
      {/* Logo */}
      <Link to="/evidence" className="flex items-center gap-2.5 hover:opacity-80 transition-opacity flex-shrink-0">
        <img src="/fabrique-icon.svg" alt="La Fabrique" width={30} height={30} className="rounded-md" />
        <span className="text-lg font-black tracking-tight hidden xl:block">La Fabrique</span>
      </Link>

      {/* Desktop nav */}
      {authenticated && (
        <nav className="hidden lg:flex flex-1 min-w-0 items-center gap-1 ml-4" aria-label={t('nav.mainNavigation', { defaultValue: 'Main navigation' })}>
          {links.map(({ to, icon: Icon, label }) => (
            <NavLink key={`${to}-${label}`} to={to} end={to === '/'} className={linkClass}>
              <Icon size={16} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
      )}

      {/* Right side */}
      <div className="ml-auto flex shrink-0 items-center gap-2">
        <LanguageSwitcher />
        {authenticated && (
          <>
            <div ref={companyMenu} className="relative" onKeyDown={event => {
              if (event.key === 'Escape') {
                setCompanyOpen(false)
                companyToggle.current?.focus()
              }
            }} onBlur={event => {
              if (!event.currentTarget.contains(event.relatedTarget)) setCompanyOpen(false)
            }}>
              <button ref={companyToggle} type="button" className="btn-ghost btn-sm" aria-haspopup="menu" aria-expanded={companyOpen}
                aria-controls="company-menu" onClick={() => {
                  setCompanyOpen(open => !open)
                  setMenuOpen(false)
                }}>
                <Building2 size={14} className="shrink-0" />
                <span className="max-w-36 truncate">{companyId !== null
                  ? companyName || t('auth.companyPlaceholder', { id: companyId, defaultValue: 'Company {{id}}' })
                  : user?.full_name || user?.email}</span>
                <ChevronDown size={14} className="shrink-0" />
              </button>
              {companyOpen && (
                <div id="company-menu" role="menu" onClick={() => setCompanyOpen(false)} className="absolute right-0 top-full mt-2 w-60 max-w-[calc(100vw-2rem)] rounded-lg border border-surface-700 bg-surface-900 p-2 shadow-lg">
                  {memberships.length > 1 && <Link role="menuitem" to="/companies/select" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('auth.switchCompany', { defaultValue: 'Switch company' })}</Link>}
                  <Link role="menuitem" to="/companies/new" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.newCompany', { defaultValue: 'New company' })}</Link>
                  <Link role="menuitem" to="/company/quotas" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.quotas', { defaultValue: 'Quotas' })}</Link>
                  <button role="menuitem" type="button" className="w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-surface-800" onClick={() => {
                    logout()
                    navigate('/login', { replace: true })
                  }}>{t('auth.logout', { defaultValue: 'Log out' })}</button>
                </div>
              )}
            </div>
          </>
        )}

        {/* Status pill — desktop only */}
        <div className="hidden xl:flex items-center gap-2 px-3 py-1.5 bg-surface-800 rounded-full border border-surface-700 text-xs text-gray-400">
          <span className="status-dot-pulse" />
          {t('nav.online', { defaultValue: 'Online' })}
        </div>

        {/* Hamburger — mobile/tablet */}
        {authenticated && <button
          ref={mobileToggle}
          aria-label={menuOpen ? t('nav.closeMenu', { defaultValue: 'Close menu' }) : t('nav.openMenu', { defaultValue: 'Open menu' })}
          aria-expanded={menuOpen}
          aria-controls="mobile-navigation"
          onClick={() => {
            setMenuOpen(v => !v)
            setCompanyOpen(false)
          }}
          className="btn-ghost btn-icon lg:hidden"
        >
          {menuOpen ? <X size={20} /> : <Menu size={20} />}
        </button>}
      </div>

      {/* Mobile dropdown */}
      {authenticated && menuOpen && (
        <div
          id="mobile-navigation"
          className="absolute top-full left-0 right-0 bg-surface-900 border-b border-surface-700 lg:hidden z-30 animate-fade-in"
          onClick={() => setMenuOpen(false)}
        >
          <nav className="flex flex-col p-3 gap-1" aria-label={t('nav.mobileNavigation', { defaultValue: 'Mobile navigation' })}>
            {links.map(({ to, icon: Icon, label }) => (
              <NavLink key={`${to}-${label}`} to={to} end={to === '/'} className={linkClass}>
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

function SprintPages() {
  const { t } = useTranslation()
  const { pathname } = useLocation()
  return <div>
    <div role="tablist" className="flex gap-2 px-4 pt-4 lg:px-6" aria-label={t('nav.sprints', { defaultValue: 'Sprints' })}>
      <Link role="tab" aria-selected={pathname === '/dashboard'} to="/dashboard" className={`btn-ghost btn-sm ${pathname === '/dashboard' ? 'text-brand-400 bg-surface-800' : ''}`}>{t('nav.board', { defaultValue: 'Board' })}</Link>
      <Link role="tab" aria-selected={pathname === '/dashboard/analytics'} to="/dashboard/analytics" className={`btn-ghost btn-sm ${pathname === '/dashboard/analytics' ? 'text-brand-400 bg-surface-800' : ''}`}>{t('nav.analytics', { defaultValue: 'Analytics' })}</Link>
    </div>
    <Outlet />
  </div>
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
              <Route path="/" element={<Navigate to="/evidence" replace />} />
              <Route path="/dashboard" element={<SprintPages />}>
                <Route index element={<Dashboard />} />
                <Route path="analytics" element={<div className="page-container"><AnalyticsDashboard sprintId={activeSprintId ?? 0} /></div>} />
              </Route>
              <Route path="/companies/new" element={<CompanyNew />} />
              <Route path="/companies/select" element={<CompanySelect />} />
              <Route path="/company/quotas" element={<div className="page-container"><QuotaPanel /></div>} />
              <Route path="/workspace/:id" element={<SprintWorkspace />} />
              <Route
                path="/governance"
                element={<div className="page-container"><GovernancePanel /></div>}
              />
              <Route path="/evidence" element={<Evidence />} />
              <Route path="/evidence/:runId" element={<EvidenceDetail />} />
              <Route
                path="/analytics"
                element={<Navigate to="/dashboard/analytics" replace />}
              />
              <Route path="/memory" element={<MemorySearch />} />
              <Route path="/generate" element={<Generate />} />
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
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AppContent />
    </BrowserRouter>
  )
}

export default App
