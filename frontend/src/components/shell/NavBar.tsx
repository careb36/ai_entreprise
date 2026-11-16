import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Menu, X, Building2, ChevronDown } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import { useAuthStore } from '../../store/useAuthStore'
import { useCurrentRole, useInbox } from '../../hooks/useQueries'
import { LanguageSwitcher } from './LanguageSwitcher'
import { StatusPill } from './StatusPill'
import { sectionForPath, useNavLinks } from './navLinks'

/** The top bar: logo, primary links, language, the Company menu, service status and the mobile menu. */
export function NavBar() {
  const [menuOpen, setMenuOpen] = useState(false)
  const [companyOpen, setCompanyOpen] = useState(false)
  const companyRef = useRef<HTMLDivElement>(null)
  const companyToggleRef = useRef<HTMLButtonElement>(null)
  const mobileToggleRef = useRef<HTMLButtonElement>(null)
  const mobileMenuRef = useRef<HTMLDivElement>(null)
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const user = useAuthStore(state => state.user)
  const expiresAt = useAuthStore(state => state.expiresAt)
  const logout = useAuthStore(state => state.logout)
  const memberships = useAuthStore(state => state.memberships)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const supportAdmin = Boolean(user?.support_admin)
  const companyName = useAppStore(state => state.companyName)
  const companyId = useAppStore(state => state.companyId)
  const authenticated = Boolean(expiresAt && expiresAt > Date.now())
  const links = useNavLinks()
  // What the company needs a person for, shown on the Review link.
  const inbox = useInbox(authenticated ? companyId : null)
  const waiting = inbox.data?.total ?? 0
  const section = sectionForPath(location.pathname)
  const companyCurrent = section === 'company'
  const companyLabel = companyId !== null
    ? companyName || t('auth.companyPlaceholder', { id: companyId, defaultValue: 'Company {{id}}' })
    : user?.full_name || user?.email || t('auth.selectCompany', { defaultValue: 'Select a company' })

  useEffect(() => {
    setMenuOpen(false)
    setCompanyOpen(false)
  }, [location])

  useEffect(() => {
    function closeOutside(event: PointerEvent) {
      const target = event.target as Node
      if (!companyRef.current?.contains(target)) setCompanyOpen(false)
      if (!mobileMenuRef.current?.contains(target) && !mobileToggleRef.current?.contains(target)) setMenuOpen(false)
    }
    document.addEventListener('pointerdown', closeOutside)
    return () => document.removeEventListener('pointerdown', closeOutside)
  }, [])

  useEffect(() => {
    if (companyOpen) companyRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
  }, [companyOpen])

  const linkClass = (isActive: boolean) =>
    `flex items-center gap-1.5 whitespace-nowrap text-sm font-medium px-2 py-2 rounded-lg transition-colors
     ${isActive
       ? 'text-white bg-brand-600/20 border border-brand-600/30'
       : 'text-gray-400 hover:text-white hover:bg-surface-800'}`

  return (
    <header className="relative h-14 bg-surface-900 border-b border-surface-700 text-white flex items-center gap-2 px-4 lg:px-6 z-20 flex-shrink-0"
      onKeyDown={event => {
        if (event.key !== 'Escape') return
        if (companyOpen) {
          setCompanyOpen(false)
          companyToggleRef.current?.focus()
        }
        if (menuOpen) {
          setMenuOpen(false)
          mobileToggleRef.current?.focus()
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
          {links.map(({ to, section: linkSection, icon: Icon, label }) => (
            <Link key={to} to={to} aria-current={section === linkSection ? 'page' : undefined} className={linkClass(section === linkSection)}>
              <Icon size={16} />
              <span>{label}</span>
              {to === '/governance' && waiting > 0 && (
                <span className="badge">
                  <span aria-hidden="true">{waiting}</span>
                  <span className="sr-only">{t('inbox.navCount', { count: waiting, defaultValue: '{{count}} waiting for you' })}</span>
                </span>
              )}
            </Link>
          ))}
        </nav>
      )}

      {/* Right side */}
      <div className="ml-auto flex shrink-0 items-center gap-2">
        <LanguageSwitcher />
        {authenticated && (
          <>
            <div ref={companyRef} className="relative" onBlur={event => {
              if (!event.currentTarget.contains(event.relatedTarget)) setCompanyOpen(false)
            }}>
              <button ref={companyToggleRef} type="button"
                className={`btn-ghost btn-sm max-w-36 xl:max-w-48 border ${companyCurrent ? 'text-white bg-brand-600/20 border-brand-600/30' : 'border-transparent'}`}
                aria-haspopup="menu" aria-expanded={companyOpen} title={companyLabel}
                aria-current={companyCurrent ? 'page' : undefined}
                onKeyDown={event => {
                  if (event.key === 'ArrowDown') {
                    event.preventDefault()
                    setCompanyOpen(true)
                  }
                }}
                aria-controls="company-menu" onClick={() => {
                  setCompanyOpen(open => !open)
                  setMenuOpen(false)
                }}>
                <Building2 size={14} className="shrink-0" /><span className="truncate">{companyLabel}</span><ChevronDown size={14} className="shrink-0" />
              </button>
              {companyOpen && (
                <div id="company-menu" role="menu" aria-label={companyLabel}
                  onClick={() => setCompanyOpen(false)}
                  onKeyDown={event => {
                    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
                    event.preventDefault()
                    const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]'))
                    const current = items.indexOf(document.activeElement as HTMLElement)
                    const next = event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1
                      : (current + (event.key === 'ArrowUp' ? -1 : 1) + items.length) % items.length
                    items[next]?.focus()
                  }}
                  className="absolute right-0 top-full mt-2 w-60 max-w-[calc(100vw-2rem)] rounded-lg border border-surface-700 bg-surface-900 p-2 shadow-lg">
                  {memberships.length > 1 && <Link role="menuitem" to="/companies/select" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('auth.switchCompany', { defaultValue: 'Switch company' })}</Link>}
                  <Link role="menuitem" to="/companies/new" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.newCompany', { defaultValue: 'New company' })}</Link>
                  <Link role="menuitem" to="/agents" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.agents', { defaultValue: 'Agents' })}</Link>
                  <Link role="menuitem" to="/company/quotas" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.quotas', { defaultValue: 'Quotas' })}</Link>
                  <Link role="menuitem" to="/company/models" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.models', { defaultValue: 'Models' })}</Link>
                  <Link role="menuitem" to="/company/procedures" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.procedures', { defaultValue: 'Procedures' })}</Link>
                  <Link role="menuitem" to="/company/specifications" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.specifications', { defaultValue: 'Specifications' })}</Link>
                  <Link role="menuitem" to="/company/documentation" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.documentation', { defaultValue: 'Documentation' })}</Link>
                  <Link role="menuitem" to="/company/wiki" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.wiki', { defaultValue: 'Wiki' })}</Link>
                  {role === 'Owner' && <Link role="menuitem" to="/company/members" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.members', { defaultValue: 'Members' })}</Link>}
                  {role === 'Owner' && <Link role="menuitem" to="/company/teams" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.teams')}</Link>}
                  {role === 'Owner' && <Link role="menuitem" to="/company/duties" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.duties')}</Link>}
                  {canManage && <Link role="menuitem" to="/company/usage" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.usage')}</Link>}
                  {role === 'Owner' && <Link role="menuitem" to="/company/sso"className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.sso', { defaultValue: 'Single sign-on' })}</Link>}
                  <Link role="menuitem" to="/company/chat" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.chatApprovals')}</Link>
                  {canManage && <Link role="menuitem" to="/company/audit-anchors" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.auditAnchors')}</Link>}
                  {canManage && <Link role="menuitem" to="/company/github" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.github', { defaultValue: 'GitHub' })}</Link>}
                  {supportAdmin && <Link role="menuitem" to="/support" className="block rounded-lg px-3 py-2 text-sm hover:bg-surface-800">{t('nav.helpDesk', { defaultValue: 'Help desk' })}</Link>}
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
        <StatusPill t={t} />

        {/* Hamburger — mobile/tablet */}
        {authenticated && <button
          ref={mobileToggleRef}
          aria-label={menuOpen ? t('nav.closeMenu', { defaultValue: 'Close menu' }) : t('nav.openMenu', { defaultValue: 'Open menu' })}
          aria-controls="mobile-menu"
          aria-expanded={menuOpen}
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
          ref={mobileMenuRef} id="mobile-menu"
          className="absolute top-full left-0 right-0 bg-surface-900 border-b border-surface-700 lg:hidden z-30 animate-fade-in"
          onClick={() => setMenuOpen(false)}
        >
          <nav className="flex flex-col p-3 gap-1" aria-label={t('nav.mobileNavigation', { defaultValue: 'Mobile navigation' })}>
            {links.map(({ to, section: linkSection, icon: Icon, label }) => (
              <Link key={to} to={to} aria-current={section === linkSection ? 'page' : undefined} className={linkClass(section === linkSection)}>
                <Icon size={16} />
                <span>{label}</span>
                {to === '/governance' && waiting > 0 && (
                  <span className="badge">
                    <span aria-hidden="true">{waiting}</span>
                    <span className="sr-only">{t('inbox.navCount', { count: waiting, defaultValue: '{{count}} waiting for you' })}</span>
                  </span>
                )}
              </Link>
            ))}
          </nav>
        </div>
      )}
    </header>
  )
}
