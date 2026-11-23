import { useEffect } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'
import { companyLabel } from '../lib/companyLabel'

export default function ProtectedRoute() {
  const location = useLocation()
  const { t } = useTranslation()
  const expiresAt = useAuthStore(state => state.expiresAt)
  const memberships = useAuthStore(state => state.memberships)
  const supportAdmin = useAuthStore(state => Boolean(state.user?.support_admin))
  const meLoaded = useAuthStore(state => state.meLoaded)
  const loadingMe = useAuthStore(state => state.loadingMe)
  const profileError = useAuthStore(state => state.profileError)
  const loadMe = useAuthStore(state => state.loadMe)
  const logout = useAuthStore(state => state.logout)
  const companyId = useAppStore(state => state.companyId)
  const companyName = useAppStore(state => state.companyName)
  const setCompany = useAppStore(state => state.setCompany)
  const authenticated = Boolean(expiresAt && expiresAt > Date.now())

  useEffect(() => {
    if (expiresAt && !authenticated) logout()
    else if (authenticated && !meLoaded && !loadingMe && !profileError) void loadMe().catch(() => {})
  }, [authenticated, expiresAt, loadMe, loadingMe, logout, meLoaded, profileError])

  useEffect(() => {
    if (meLoaded && !loadingMe && !profileError && memberships.length === 1) {
      const membership = memberships[0]
      if (companyId !== membership.company_id) {
        setCompany(membership.company_id, companyLabel(memberships, membership.company_id, t))
      }
    }
  }, [companyId, loadingMe, memberships, meLoaded, profileError, setCompany, t])

  // The selected company's name as the backend sends it: a name kept from
  // before (the "Company <id>" placeholder, or a renamed company) is replaced.
  useEffect(() => {
    if (!meLoaded || companyId === null) return
    const name = memberships.find(m => m.company_id === companyId)?.company_name?.trim()
    if (name && name !== companyName) setCompany(companyId, name)
  }, [companyId, companyName, memberships, meLoaded, setCompany])

  if (!authenticated) {
    const from = `${location.pathname}${location.search}${location.hash}`
    return <Navigate to={`/login?from=${encodeURIComponent(from)}`} replace />
  }

  if (profileError) {
    return (
      <div className="page-container">
        <p role="alert">{profileError}</p>
        <button className="btn-primary mt-4" onClick={() => void loadMe().catch(() => {})}>
          {t('auth.retry', { defaultValue: 'Try again' })}
        </button>
      </div>
    )
  }

  if (!meLoaded || loadingMe) {
    return <div role="status" className="page-container">{t('auth.loading', { defaultValue: 'Loading your account…' })}</div>
  }

  // Support tickets are not company data: a support admin reaches the help
  // desk without belonging to, or having selected, a company.
  if (supportAdmin && location.pathname === '/support') {
    return <Outlet />
  }

  // A person's invitations are not company data: they are how someone with no
  // company yet joins one, so the page is open without a membership.
  if (location.pathname === '/invitations') {
    return <Outlet />
  }

  const isCompanyRoute = location.pathname === '/companies/new'
  if (memberships.length === 0 && !isCompanyRoute) {
    return <Navigate to="/companies/new" replace />
  }

  // Intentional (#61): with a single membership there is nothing to choose,
  // so the selector sends the user home. The "Switch company" entry in the
  // company menu only appears with two or more memberships.
  if (memberships.length === 1 && location.pathname === '/companies/select') {
    return <Navigate to="/" replace />
  }

  if (memberships.length > 1) {
    const hasSelectedCompany = memberships.some(membership => membership.company_id === companyId)
    if (!hasSelectedCompany && location.pathname !== '/companies/select') {
      const from = `${location.pathname}${location.search}${location.hash}`
      return <Navigate to={`/companies/select?from=${encodeURIComponent(from)}`} replace />
    }
  }

  return <Outlet />
}
