import { render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import App from '../App'
import { api } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'
import { useAppStore } from '../store/useAppStore'

it('redirects the authenticated home route to Evidence', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(api, 'capabilities').mockResolvedValue({ agent_graph: false, memory: false, github: false, auth_required: true })
  vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ agent_runs: [] } as never)
  useAuthStore.setState({
    token: 'test-token', expiresAt: Date.now() + 60_000, meLoaded: true,
    memberships: [{ company_id: 1, human_role: 'Owner' }],
  })
  useAppStore.setState({ companyId: 1, companyName: 'Example' })
  window.history.replaceState(null, '', '/')
  try {
    render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
    await waitFor(() => expect(window.location.pathname).toBe('/evidence'))
  } finally {
    client.clear()
    vi.restoreAllMocks()
    useAuthStore.getState().logout()
    window.history.replaceState(null, '', '/')
  }
})
