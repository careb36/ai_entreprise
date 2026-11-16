/** What the app's tests share: rendering inside the query client and i18n, the API stubs and clean-up around every test, and a signed-in Owner of one company (`App*.test.tsx`). */
import { afterEach, beforeEach, vi } from 'vitest'
import { render as rtlRender } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import i18n from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { api, setOnUnauthorized } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'
import { useAppStore } from '../store/useAppStore'
import { queryClient } from '../lib/queryClient'

export function render(ui: React.ReactElement) {
  return rtlRender(<QueryClientProvider client={queryClient}><I18nextProvider i18n={i18n}>{ui}</I18nextProvider></QueryClientProvider>)
}

/** Registers the stubs before and the clean-up after every test of the calling file. */
export function installAppTestHooks() {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: null, companyName: null, activeSprintId: null })
    vi.spyOn(api, 'capabilities').mockResolvedValue({ agent_graph: false, memory: false, github: false, auth_required: true })
    vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ agent_runs: [] } as never)
    vi.spyOn(api, 'getInbox').mockResolvedValue({ blocked_tasks: [], proposals: [], pending_runs: 0, total: 0 })
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'healthy', timestamp: '' })
    vi.spyOn(api, 'getOrchestrationHealth').mockResolvedValue({} as never)
    vi.spyOn(api, 'listSprints').mockResolvedValue([])
    vi.spyOn(api, 'workspaceStatus').mockResolvedValue({ connected: false, host: null })
  })

  afterEach(() => {
    queryClient.clear()
    setOnUnauthorized(null)
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    useAuthStore.getState().logout()
    window.history.replaceState(null, '', '/')
    void i18n.changeLanguage('en')
  })
}

export function authenticate() {
  useAuthStore.setState({
    expiresAt: Date.now() + 60_000,
    meLoaded: true,
    loadingMe: false,
    memberships: [{ company_id: 1, human_role: 'Owner' }],
  })
  useAppStore.getState().setCompany(1, 'Example company')
}
