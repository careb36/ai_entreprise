import { useEffect } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

export default function ProtectedRoute() {
  const location = useLocation()
  const { t } = useTranslation()
  const token = useAuthStore(state => state.token)
  const expiresAt = useAuthStore(state => state.expiresAt)
  const memberships = useAuthStore(state => state.memberships)
  const meLoaded = useAuthStore(state => state.meLoaded)
  const loadingMe = useAuthStore(state => state.loadingMe)
  const profileError = useAuthStore(state => state.profileError)
  const loadMe = useAuthStore(state => state.loadMe)
  const logout = useAuthStore(state => state.logout)
  const companyId = useAppStore(state => state.companyId)
  const setCompany = useAppStore(state => state.setCompany)
  const authenticated = Boolean(token && expiresAt && expiresAt > Date.now())

  useEffect(() => {
    if (token && !authenticated) logout()
    else if (authenticated && !meLoaded && !loadingMe) void loadMe().catch(() => {})
  }, [authenticated, loadMe, loadingMe, logout, meLoaded, token])

  useEffect(() => {
    if (meLoaded && memberships.length === 1) {
      const membership = memberships[0]
      if (companyId !== membership.company_id) {
        setCompany(
          membership.company_id,
          t('auth.companyPlaceholder', {
            id: membership.company_id,
            defaultValue: 'Company {{id}}',
          }),
        )
      }
    }
  }, [companyId, memberships, meLoaded, setCompany, t])

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

  const isCompanyRoute = location.pathname === '/companies/new'
  if (memberships.length === 0 && !isCompanyRoute) {
    return <Navigate to="/companies/new" replace />
  }

  if (memberships.length === 1 && location.pathname === '/companies/select') {
    return <Navigate to="/" replace />
  }

  if (memberships.length > 1) {
    const hasSelectedCompany = memberships.some(membership => membership.company_id === companyId)
    if (!hasSelectedCompany && location.pathname !== '/companies/select') {
      return <Navigate to="/companies/select" replace />
    }
  }

  return <Outlet />
}
