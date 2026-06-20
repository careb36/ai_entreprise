import { lazy, Suspense, useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Link, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from './store/useAppStore'
import { useAuthStore } from './store/authStore'
import { ProtectedRoute } from './components/ProtectedRoute'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ToastContainer } from './components/Toast'
import {
  LayoutDashboard, ShieldCheck, PieChart, MonitorPlay,
  Globe, Database, Code2, Menu, X, Building2, LogOut, UserCircle,
} from 'lucide-react'
import { STORAGE_KEY_LANG } from './lib/constants'
import { getInitials } from './lib/avatar'

// ── Lazy page imports ─────────────────────────────────────────────────────────

const Dashboard      = lazy(() => import('./pages/Dashboard'))
const Home           = lazy(() => import('./pages/Home'))
const CompanyNew     = lazy(() => import('./pages/CompanyNew'))
const SprintWorkspaceModule = lazy(() => import('./pages/SprintWorkspace').then(m => ({ default: m.SprintWorkspace })))
const MemorySearch   = lazy(() => import('./pages/MemorySearch'))
const Generate       = lazy(() => import('./pages/Generate'))
const Glassroom      = lazy(() => import('./pages/Glassroom'))
const NotFound       = lazy(() => import('./pages/NotFound'))
const TaskDetails    = lazy(() => import('./pages/TaskDetails'))
const LoginPage      = lazy(() => import('./pages/LoginPage'))
const RegisterPage   = lazy(() => import('./pages/RegisterPage'))
const ProfilePage    = lazy(() => import('./pages/ProfilePage'))
const GovernancePanelPage = lazy(() => import('./components/GovernancePanel').then(m => ({ default: m.GovernancePanel })))
const AnalyticsDashboardPage = lazy(() => import('./components/AnalyticsDashboard').then(m => ({ default: m.AnalyticsDashboard })))
import { SupportBot } from './components/SupportBot'

// ── Page loader fallback ──────────────────────────────────────────────────────

function PageLoader() {
  return (
    <div className="flex items-center justify-center h-full min-h-[200px]">
      <span className="w-5 h-5 rounded-full border-2 border-brand-500 border-t-transparent animate-spin" />
    </div>
  )
}

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

// ── User menu ─────────────────────────────────────────────────────────────────

function UserMenu() {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  const clearAuth = useAuthStore((s) => s.clearAuth)
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  if (!user) return null

  const roleLabel = user.role
    ? user.role.charAt(0).toUpperCase() + user.role.slice(1).toLowerCase()
    : 'User'
  const initials = getInitials(user.full_name, user.email)

  function handleLogout() {
    setOpen(false)
    clearAuth()
    window.location.replace('/login')
  }

  function goToProfile() {
    setOpen(false)
    navigate('/profile')
  }

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-label={t('auth.loggedInAs') + ' ' + user.email}
        className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-surface-800 transition-colors"
      >
        <span className="w-7 h-7 rounded-full bg-brand-600/30 border border-brand-600/50 flex items-center justify-center text-[11px] font-bold text-brand-300">
          {initials}
        </span>
        <span className="hidden sm:block text-xs text-gray-400 max-w-[120px] truncate">{user.email}</span>
      </button>

      {open && (
        <>
          {/* Backdrop */}
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full mt-1.5 z-50 min-w-[180px] card p-1.5 shadow-card-md animate-fade-in">
            <div className="px-3 py-2 border-b border-surface-700 mb-1">
              <p className="text-xs font-medium text-white truncate">{user.email}</p>
              <p className="text-[11px] text-gray-500">{roleLabel}</p>
            </div>
            <button
              onClick={goToProfile}
              className="w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-sm text-gray-300 hover:bg-surface-700 hover:text-white transition-colors"
            >
              <UserCircle size={14} />
              {t('auth.myProfile')}
            </button>
            <button
              onClick={handleLogout}
              className="w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-sm text-gray-300 hover:bg-surface-700 hover:text-white transition-colors"
            >
              <LogOut size={14} />
              {t('auth.logOut')}
            </button>
          </div>
        </>
      )}
    </div>
  )
}

// ── Nav links config ──────────────────────────────────────────────────────────

function useNavLinks() {
  const { t } = useTranslation()
  const activeSprintId = useAppStore(state => state.activeSprintId)
   const workspaceTo = activeSprintId ? `/workspace/${activeSprintId}` : '/glassroom'
  return [
     { to: '/',           icon: LayoutDashboard, label: t('nav.home') },
     { to: '/companies/new', icon: Building2, label: t('nav.newCompany') },
     { to: '/dashboard',  icon: LayoutDashboard, label: t('nav.dashboard') },
     { to: workspaceTo,   icon: MonitorPlay, label: t('nav.workspace') },
     { to: '/governance', icon: ShieldCheck,    label: t('nav.governance') },
    { to: '/analytics',  icon: PieChart,       label: t('nav.analytics') },
    { to: '/memory',     icon: Database,       label: t('nav.memory') },
    { to: '/generate',   icon: Code2,          label: t('nav.generate') },
  ]
}

// ── NavBar ────────────────────────────────────────────────────────────────────

function NavBar() {
  const [menuOpen, setMenuOpen] = useState(false)
  const links = useNavLinks()

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
      <nav className="hidden lg:flex items-center gap-1 ml-8" aria-label="Main navigation">
        {links.map(({ to, icon: Icon, label }) => (
          <NavLink key={to} to={to} end={to === '/'} className={linkClass}>
            <Icon size={16} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>

      {/* Right side */}
      <div className="ml-auto flex items-center gap-2">
        <LanguageSwitcher />
        <UserMenu />

        {/* Status pill — desktop only */}
        <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 bg-surface-800 rounded-full border border-surface-700 text-xs text-gray-400">
          <span className="status-dot-pulse" />
          Online
        </div>

        {/* Hamburger — mobile/tablet */}
        <button
          aria-label={menuOpen ? 'Close menu' : 'Open menu'}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen(v => !v)}
          className="btn-ghost btn-icon lg:hidden"
        >
          {menuOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
      </div>

      {/* Mobile dropdown */}
      {menuOpen && (
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

  return (
    <div className="min-h-screen bg-surface-950 flex flex-col relative">
      <NavBar />
      <main className="flex-1 overflow-auto">
        <ErrorBoundary>
          <Suspense fallback={<PageLoader />}>
            <Routes>
              {/* Public routes */}
              <Route path="/login" element={<LoginPage />} />
              <Route path="/register" element={<RegisterPage />} />

              {/* Protected routes */}
              <Route path="/" element={<ProtectedRoute><Home /></ProtectedRoute>} />
              <Route path="/dashboard" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
              <Route path="/companies/new" element={<ProtectedRoute><CompanyNew /></ProtectedRoute>} />
              <Route path="/workspace/:id" element={<ProtectedRoute><SprintWorkspaceModule /></ProtectedRoute>} />
              <Route
                path="/governance"
                element={<ProtectedRoute><div className="page-container"><GovernancePanelPage /></div></ProtectedRoute>}
              />
              <Route
                path="/analytics"
                element={<ProtectedRoute><div className="page-container"><AnalyticsDashboardPage sprintId={activeSprintId ?? 0} /></div></ProtectedRoute>}
              />
              <Route path="/memory" element={<ProtectedRoute><MemorySearch /></ProtectedRoute>} />
              <Route path="/generate" element={<ProtectedRoute><Generate /></ProtectedRoute>} />
              <Route path="/glassroom" element={<ProtectedRoute><Glassroom /></ProtectedRoute>} />
              <Route path="/workspace/:sprintId/task/:taskId" element={<ProtectedRoute><TaskDetails /></ProtectedRoute>} />
              <Route path="/task/:taskId" element={<ProtectedRoute><TaskDetails /></ProtectedRoute>} />
              <Route path="/profile" element={<ProtectedRoute><ProfilePage /></ProtectedRoute>} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Suspense>
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
