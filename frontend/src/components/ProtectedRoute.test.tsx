import { beforeEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import ProtectedRoute from './ProtectedRoute'
import CompanySelect from '../pages/CompanySelect'
import { useAuthStore } from '../store/useAuthStore'
import { useAppStore } from '../store/useAppStore'

function CurrentPath() {
  const location = useLocation()
  return <output data-testid="current-path">{location.pathname}{location.search}</output>
}

function renderRoutes(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/login" element={<CurrentPath />} />
        <Route element={<ProtectedRoute />}>
          <Route path="/" element={<CurrentPath />} />
          <Route path="/dashboard" element={<CurrentPath />} />
          <Route path="/companies/new" element={<CurrentPath />} />
          <Route path="/companies/select" element={<CompanySelect />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

function authenticate(memberships: { company_id: number; human_role: 'Owner' | 'Manager' | 'Viewer' }[]) {
  useAuthStore.setState({
    token: 'valid-token',
    expiresAt: Date.now() + 60_000,
    user: { user_id: 1, email: 'user@example.com', full_name: 'User' },
    memberships,
    meLoaded: true,
    loadingMe: false,
    profileError: null,
  })
}

describe('ProtectedRoute company access', () => {
  beforeEach(() => {
    useAuthStore.getState().logout()
    useAppStore.getState().setCompany(null, null)
  })

  it('redirects an unauthenticated visitor to login and preserves the requested route', async () => {
    renderRoutes('/dashboard?view=recent')

    expect(await screen.findByTestId('current-path')).toHaveTextContent('/login?from=%2Fdashboard%3Fview%3Drecent')
  })

  it('selects the only membership automatically', async () => {
    authenticate([{ company_id: 14, human_role: 'Owner' }])
    renderRoutes('/dashboard')

    expect(await screen.findByTestId('current-path')).toHaveTextContent('/dashboard')
    await waitFor(() => expect(useAppStore.getState().companyId).toBe(14))
    expect(useAppStore.getState().companyName).toBe('Company 14')
  })

  it('shows each membership and role when the user has multiple companies', async () => {
    authenticate([
      { company_id: 14, human_role: 'Owner' },
      { company_id: 28, human_role: 'Viewer' },
    ])
    renderRoutes('/dashboard')

    expect(await screen.findByRole('heading', { name: 'Select a company' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Company 14.*Owner/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Company 28.*Viewer/ })).toBeInTheDocument()
  })

  it('requires company selection before opening company creation with multiple memberships', async () => {
    authenticate([
      { company_id: 14, human_role: 'Owner' },
      { company_id: 28, human_role: 'Viewer' },
    ])
    renderRoutes('/companies/new')

    expect(await screen.findByRole('heading', { name: 'Select a company' })).toBeInTheDocument()
  })

  it('redirects users with no memberships to create a company', async () => {
    authenticate([])
    renderRoutes('/dashboard')

    expect(await screen.findByTestId('current-path')).toHaveTextContent('/companies/new')
  })

  it('logout clears both auth and company stores', () => {
    authenticate([{ company_id: 14, human_role: 'Owner' }])
    useAppStore.getState().setCompany(14, 'Company 14')
    useAppStore.getState().setActiveSprint(5)

    useAuthStore.getState().logout()

    expect(useAuthStore.getState().token).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
    expect(useAppStore.getState().companyId).toBeNull()
    expect(useAppStore.getState().companyName).toBeNull()
    expect(useAppStore.getState().activeSprintId).toBeNull()
  })
})
