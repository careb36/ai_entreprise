import { useState } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from './store/useAppStore'
import Dashboard from './pages/Dashboard'
import Home from './pages/Home'
import CompanyNew from './pages/CompanyNew'
import { SprintWorkspace } from './pages/SprintWorkspace'
import { GovernancePanel } from './components/GovernancePanel'
import { AnalyticsDashboard } from './components/AnalyticsDashboard'
import { SupportBot } from './components/SupportBot'
import MemorySearch from './pages/MemorySearch'
import Generate from './pages/Generate'
import Glassroom from './pages/Glassroom'
import NotFound from './pages/NotFound'
import TaskDetails from './pages/TaskDetails'
import { ErrorBoundary } from './components/ErrorBoundary'
import { ToastContainer } from './components/Toast'
import {
  LayoutDashboard, ShieldCheck, PieChart, MonitorPlay,
  Globe, Database, Code2, Menu, X, Building2
} from 'lucide-react'
import { STORAGE_KEY_LANG } from './lib/constants'

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
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/companies/new" element={<CompanyNew />} />
            <Route path="/workspace/:id" element={<SprintWorkspace />} />
            <Route
              path="/governance"
              element={<div className="page-container"><GovernancePanel /></div>}
            />
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
