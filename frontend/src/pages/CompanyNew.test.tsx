import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useMutation } from '@tanstack/react-query'
import CompanyNew from './CompanyNew'
import ProtectedRoute from '../components/ProtectedRoute'
import { api } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'
import { useAppStore } from '../store/useAppStore'

vi.mock('../hooks/useQueries', () => ({
  useModels: () => ({ data: { models: [] } }),
  useDiscoverTeam: () => ({ isPending: false, error: null }),
  useInitCompany: () => useMutation({ mutationFn: api.initCompany }),
}))

function CurrentPath() {
  const location = useLocation()
  return <output data-testid="current-path">{location.pathname}</output>
}

describe('company creation profile refresh', () => {
  beforeEach(() => {
    useAuthStore.getState().logout()
    useAuthStore.setState({
      token: 'valid-token',
      expiresAt: Date.now() + 60_000,
      meLoaded: true,
    })
  })

  afterEach(() => vi.restoreAllMocks())

  it('keeps the created company and workspace when refresh fails (issue #6)', async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    const init = vi.spyOn(api, 'initCompany').mockResolvedValue({
      company_id: 14, sprint_id: 5, task_count: 0, message: 'Created',
    })
    let rejectProfile!: (error: Error) => void
    const me = vi.spyOn(api, 'me')
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectProfile = reject }))
      .mockResolvedValue({
        user_id: 1, email: 'user@example.com', full_name: 'User',
        memberships: [{ company_id: 14, human_role: 'Owner' }],
      })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/companies/new']}>
          <CurrentPath />
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route path="/companies/new" element={<CompanyNew />} />
              <Route path="/workspace/5" element={<div>Created workspace</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Next →' }))
    fireEvent.change(screen.getByPlaceholderText('Acme Corp'), { target: { value: 'New Factory' } })
    for (let step = 1; step < 5; step++) {
      fireEvent.click(screen.getByRole('button', { name: 'Next →' }))
    }
    fireEvent.click(screen.getByRole('button', { name: /Initialize Factory/ }))
    expect(await screen.findByRole('status')).toBeInTheDocument()
    expect(me).toHaveBeenCalledOnce()

    await act(async () => { rejectProfile(new Error('Profile unavailable')) })

    expect(screen.getByRole('alert')).toHaveTextContent('Profile unavailable')
    expect(useAppStore.getState()).toMatchObject({ companyId: 14, companyName: 'New Factory' })
    expect(screen.getByTestId('current-path')).toHaveTextContent('/workspace/5')

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Created workspace')).toBeInTheDocument()
    expect(init).toHaveBeenCalledOnce()
    client.clear()
  })
})
