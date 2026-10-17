import { useEffect, useState } from 'react'
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
import { CompanyQuotas } from './components/CompanyQuotas'
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

function LanguageSwitcher() {
  const { i18n } = useTranslation()
  const next = i18n.language === 'en' ? 'ES' : 'EN'

  function toggle() {
    const lang = i18n.language === 'en' ? 'es' : 'en'
    i18n.changeLanguage(lang)
    localStorage.setItem(STORAGE_KEY_LANG, lang)
  }

  return (
    <button
      onClick={toggle}
      aria-label={`Switch to ${next}`}
      className="btn-ghost btn-sm flex items-center gap-1.5 border border-surface-700"
    >
      <Globe size={14} />
      {next}
    </button>
  )
}

// ── Nav links config ──────────────────────────────────────────────────────────

function useNavLinks(authenticated: boolean) {
  const { t } = useTranslation()
  const { data, isError } = useCapabilities(authenticated)
  return [
    { to: '/evidence', icon: ClipboardCheck, label: t('nav.evidence') },
    { to: '/governance', icon: ShieldCheck, label: t('nav.review') },
    { to: '/dashboard', icon: LayoutDashboard, label: t('nav.sprints') },
    ...(!isError && data?.agent_graph === true ? [{ to: '/generate', icon: Code2, label: t('nav.generate') }] : []),
    ...(!isError && data?.memory === true ? [{ to: '/memory', icon: Database, label: t('nav.memory') }] : []),
  ]
}

// ── NavBar ────────────────────────────────────────────────────────────────────

function NavBar() {
  const [menuOpen, setMenuOpen] = useState(false)
  const [companyOpen, setCompanyOpen] = useState(false)
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const user = useAuthStore(state => state.user)
  const token = useAuthStore(state => state.token)
  const expiresAt = useAuthStore(state => state.expiresAt)
  const logout = useAuthStore(state => state.logout)
  const memberships = useAuthStore(state => state.memberships)
  const companyName = useAppStore(state => state.companyName)
  const authenticated = Boolean(token && expiresAt && expiresAt > Date.now())
  const links = useNavLinks(authenticated)

  useEffect(() => {
    setMenuOpen(false)
    setCompanyOpen(false)
  }, [location])

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-1.5 text-sm font-medium px-3 py-2 rounded-lg transition-colors
     ${isActive
       ? 'text-white bg-brand-600/20 border border-brand-600/30'
       : 'text-gray-400 hover:text-white hover:bg-surface-800'}`

  return (
    <header className="relative min-h-14 bg-surface-900 border-b border-surface-700 text-white flex items-center gap-2 px-4 lg:px-6 z-20 flex-shrink-0">
      {/* Logo */}
      <Link to="/" className="flex items-center gap-2.5 hover:opacity-80 transition-opacity flex-shrink-0">
        <img src="/fabrique-icon.svg" alt="La Fabrique" width={30} height={30} className="rounded-md" />
        <span className="text-lg font-black tracking-tight hidden xs:block">La Fabrique</span>
      </Link>

      {/* Desktop nav */}
      {authenticated && (
        <nav className="hidden xl:flex flex-1 min-w-0 flex-wrap items-center gap-1 ml-4" aria-label={t('nav.mainNavigation')}>
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
            <div className="relative" onKeyDown={event => {
              if (event.key === 'Escape') setCompanyOpen(false)
            }} onBlur={event => {
              if (!event.currentTarget.contains(event.relatedTarget)) setCompanyOpen(false)
            }}>
              <button type="button" className="btn-ghost btn-sm" aria-expanded={companyOpen}
                aria-controls="company-menu" onClick={() => {
                  setCompanyOpen(open => !open)
                  setMenuOpen(false)
                }}>
                <Building2 size={14} />{t('nav.company')}<ChevronDown size={14} />
              </button>
              {companyOpen && (
                <div id="company-menu" className="absolute right-0 top-full mt-2 w-60 max-w-[calc(100vw-2rem)] rounded-lg border border-surface-700 bg-surface-900 p-2 shadow-lg">
                  <p className="truncate px-3 py-2 text-sm font-semibold" title={companyName ?? undefined}>
                    {companyName ?? t('auth.selectCompany')}
                  </p>
                  <p className="truncate px-3 pb-2 text-xs text-gray-400" title={user?.full_name || user?.email}>{user?.full_name || user?.email}</p>
                  {memberships.length > 1 && <Link to="/companies/select" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('auth.switchCompany')}</Link>}
                  <Link to="/companies/new" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.newCompany')}</Link>
                  <Link to="/companies/quotas" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.quotas')}</Link>
                  <button type="button" className="w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-surface-800" onClick={() => {
                    logout()
                    navigate('/login', { replace: true })
                  }}>{t('auth.logout', { defaultValue: 'Log out' })}</button>
                </div>
              )}
            </div>
          </>
        )}

        {/* Status pill — desktop only */}
        <div className="hidden lg:flex items-center gap-2 px-3 py-1.5 bg-surface-800 rounded-full border border-surface-700 text-xs text-gray-400">
          <span className="status-dot-pulse" />
          {t('dashboard.online')}
        </div>

        {/* Hamburger — mobile/tablet */}
        {authenticated && <button
          aria-label={t(menuOpen ? 'nav.closeMenu' : 'nav.openMenu')}
          aria-expanded={menuOpen}
          onClick={() => {
            setMenuOpen(v => !v)
            setCompanyOpen(false)
          }}
          className="btn-ghost btn-icon xl:hidden"
        >
          {menuOpen ? <X size={20} /> : <Menu size={20} />}
        </button>}
      </div>

      {/* Mobile dropdown */}
      {authenticated && menuOpen && (
        <div
          className="absolute top-full left-0 right-0 bg-surface-900 border-b border-surface-700 xl:hidden z-30 animate-fade-in"
          onClick={() => setMenuOpen(false)}
        >
          <nav className="flex flex-col p-3 gap-1" aria-label={t('nav.mobileNavigation')}>
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
  return <div>
    <nav className="flex gap-2 px-4 pt-4 lg:px-6" aria-label={t('nav.sprints')}>
      <NavLink to="/dashboard" end className={({ isActive }) => `btn-ghost btn-sm ${isActive ? 'text-brand-400 bg-surface-800' : ''}`}>{t('nav.overview')}</NavLink>
      <NavLink to="/dashboard/analytics" className={({ isActive }) => `btn-ghost btn-sm ${isActive ? 'text-brand-400 bg-surface-800' : ''}`}>{t('nav.analytics')}</NavLink>
    </nav>
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
              <Route path="/companies/quotas" element={<div className="page-container"><CompanyQuotas /></div>} />
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
