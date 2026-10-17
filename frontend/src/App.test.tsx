import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render as rtlRender, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import i18n from 'i18next'
import App from './App'
import { api, ApiError, setOnUnauthorized } from './services/api'
import { useAuthStore } from './store/useAuthStore'
import { useAppStore } from './store/useAppStore'
import { queryClient } from './lib/queryClient'
import es from './i18n/es.json'

function render(ui: React.ReactElement) {
  return rtlRender(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

beforeEach(() => {
  queryClient.clear()
  useAppStore.setState({ companyId: null, companyName: null, activeSprintId: null })
  vi.spyOn(api, 'get').mockResolvedValue({ agent_graph: false, memory: false })
  vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ agent_runs: [] } as never)
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

describe('unauthorized navigation', () => {
  it('does not nest the return destination when already on login (issue #1)', async () => {
    vi.mocked(api.get).mockRestore()
    useAuthStore.getState().logout()
    window.history.replaceState(null, '', '/login?from=%2Fevidence%2F5')
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }))
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Log in' })).toBeInTheDocument()

    await act(async () => {
      useAuthStore.setState({ token: 'rejected-token', expiresAt: Date.now() + 60_000 })
      await expect(api.get('/v1/protected')).rejects.toBeInstanceOf(ApiError)
    })

    expect(useAuthStore.getState().token).toBeNull()
    expect(window.location.pathname).toBe('/login')
    expect(window.location.search).toBe('?from=%2Fevidence%2F5')
  })
})

describe('app navigation', () => {
  it.each([0, 1, 2])('offers company switching only for multiple memberships (%i)', async count => {
    useAuthStore.setState({
      token: 'signed-token',
      expiresAt: Date.now() + 60_000,
      meLoaded: true,
      loadingMe: count === 0,
      memberships: Array.from({ length: count }, (_, index) => ({
        company_id: index + 1,
        human_role: 'Owner',
      })),
    })
    useAppStore.getState().setCompany(1, 'Company 1')
    useAppStore.getState().setActiveSprint(42)
    window.history.replaceState(null, '', '/')
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Company' }))

    if (count < 2) {
      expect(screen.queryByRole('link', { name: 'Switch company' })).not.toBeInTheDocument()
      return
    }

    fireEvent.click(screen.getByRole('link', { name: 'Switch company' }))
    expect(await screen.findByRole('heading', { name: 'Select a company' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Company 2\s*Owner/ }))
    await waitFor(() => expect(window.location.pathname).toBe('/evidence'))
    expect(useAppStore.getState().companyId).toBe(2)
    expect(useAppStore.getState().activeSprintId).toBeNull()
  })

  function authenticate() {
    useAuthStore.setState({
      token: 'signed-token',
      expiresAt: Date.now() + 60_000,
      meLoaded: true,
      loadingMe: false,
      memberships: [{ company_id: 1, human_role: 'Owner' }],
    })
    useAppStore.getState().setCompany(1, 'Example company')
  }

  describe('evidence-first navigation', () => {
    it.each([
      ['en', 'Evidence', 'Review', 'Sprints', 'Company', 'Open menu', 'Mobile navigation'],
      ['es', 'Evidencia', 'Revisión', 'Sprints', 'Compañía', 'Abrir menú', 'Navegación móvil'],
    ])('orders primary destinations in %s on desktop and mobile', async (lang, evidence, review, sprints, company, openMenu, mobileNav) => {
      i18n.addResourceBundle('es', 'translation', es)
      await i18n.changeLanguage(lang)
      authenticate()
      render(<App />)
      await waitFor(() => expect(window.location.pathname).toBe('/evidence'))
      const nav = screen.getByRole('navigation', { name: lang === 'es' ? 'Navegación principal' : 'Main navigation' })
      expect(within(nav).getAllByRole('link').map(link => link.textContent)).toEqual([evidence, review, sprints])
      expect(within(nav).getAllByRole('link').filter(link => link.getAttribute('aria-current') === 'page')).toHaveLength(1)
      expect(screen.getByRole('button', { name: company })).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: openMenu }))
      expect(within(screen.getByRole('navigation', { name: mobileNav })).getAllByRole('link').map(link => link.textContent)).toEqual([evidence, review, sprints])
    })

    it.each([
      [false, false], [true, false], [false, true], [true, true],
    ])('shows optional links only for reported capabilities (graph %s, memory %s)', async (agent_graph, memory) => {
      authenticate()
      vi.mocked(api.get).mockResolvedValue({ agent_graph, memory })
      render(<App />)
      const nav = screen.getByRole('navigation', { name: 'Main navigation' })
      await waitFor(() => expect(api.get).toHaveBeenCalledWith('/v1/capabilities'))
      await waitFor(() => {
        expect(Boolean(within(nav).queryByRole('link', { name: 'Generate' }))).toBe(agent_graph)
        expect(Boolean(within(nav).queryByRole('link', { name: 'Memory' }))).toBe(memory)
      })
    })

    it('hides optional links while capabilities load or fail', async () => {
      authenticate()
      let reject!: (reason: Error) => void
      vi.mocked(api.get).mockImplementation(() => new Promise((_resolve, rejectPromise) => { reject = rejectPromise }))
      render(<App />)
      const nav = screen.getByRole('navigation', { name: 'Main navigation' })
      expect(within(nav).queryByRole('link', { name: 'Generate' })).not.toBeInTheDocument()
      expect(within(nav).queryByRole('link', { name: 'Memory' })).not.toBeInTheDocument()
      await act(async () => reject(new ApiError(404, 'not available')))
      expect(within(nav).queryByRole('link', { name: 'Generate' })).not.toBeInTheDocument()
      expect(within(nav).queryByRole('link', { name: 'Memory' })).not.toBeInTheDocument()
    })

    it('keeps no-membership onboarding ahead of the landing redirect', async () => {
      authenticate()
      useAuthStore.setState({ memberships: [] })
      render(<App />)
      await waitFor(() => expect(window.location.pathname).toBe('/companies/new'))
    })

    it('opens quotas from Company and retains logout', async () => {
      authenticate()
      vi.spyOn(api, 'getQuotas').mockResolvedValue([{ id: 7, unit: 'Engineering', used: 80, limit: 100 }])
      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Company' }))
      expect(screen.getByText('Example company')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'New company' })).toHaveAttribute('href', '/companies/new')
      fireEvent.click(screen.getByRole('link', { name: 'Quotas' }))
      expect(await screen.findByText('Engineering')).toBeInTheDocument()
      expect(window.location.pathname).toBe('/companies/quotas')
      expect(screen.queryByText('No agent runs awaiting review')).not.toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: 'Company' }))
      fireEvent.click(screen.getByRole('button', { name: 'Log out' }))
      await waitFor(() => expect(window.location.pathname).toBe('/login'))
      expect(useAuthStore.getState().token).toBeNull()
    })

    it('closes the Company dropdown with Escape', () => {
      authenticate()
      render(<App />)
      const button = screen.getByRole('button', { name: 'Company' })
      fireEvent.click(button)
      fireEvent.keyDown(button, { key: 'Escape' })
      expect(button).toHaveAttribute('aria-expanded', 'false')
    })

    it.each(['/dashboard/analytics', '/analytics'])('nests analytics in Sprints at %s', async path => {
      authenticate()
      window.history.replaceState(null, '', path)
      vi.spyOn(api, 'getOrchestrationHealth').mockResolvedValue({} as never)
      render(<App />)
      await waitFor(() => expect(window.location.pathname).toBe('/dashboard/analytics'))
      const primary = screen.getByRole('navigation', { name: 'Main navigation' })
      expect(within(primary).getByRole('link', { name: 'Sprints' })).toHaveAttribute('aria-current', 'page')
      expect(within(primary).queryByRole('link', { name: 'Analytics' })).not.toBeInTheDocument()
      const tabs = screen.getByRole('navigation', { name: 'Sprints' })
      expect(within(tabs).getByRole('link', { name: 'Analytics' })).toHaveAttribute('aria-current', 'page')
      expect(within(tabs).getByRole('link', { name: 'Overview' })).not.toHaveAttribute('aria-current')
    })

    it('removes the duplicate Glassroom route', async () => {
      authenticate()
      window.history.replaceState(null, '', '/glassroom')
      render(<App />)
      expect(await screen.findByText('404')).toBeInTheDocument()
      expect(screen.queryByLabelText('Sprint ID')).not.toBeInTheDocument()
    })
  })

  it('does not emit React Router future-flag warnings', () => {
    useAuthStore.getState().logout()
    window.history.replaceState(null, '', '/login')
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    render(<App />)

    expect(warn.mock.calls.flat().join(' ')).not.toMatch(/React Router Future Flag Warning/)
  })
})
