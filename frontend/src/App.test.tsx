import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render as rtlRender, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import i18n from 'i18next'
import { createInstance } from 'i18next'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import App, { LanguageSwitcher } from './App'
import en from './i18n/en.json'
import { STORAGE_KEY_LANG } from './lib/constants'
import { api, ApiError, setOnUnauthorized } from './services/api'
import { useAuthStore } from './store/useAuthStore'
import { useAppStore } from './store/useAppStore'
import { queryClient } from './lib/queryClient'
import es from './i18n/es.json'

function render(ui: React.ReactElement) {
  return rtlRender(<QueryClientProvider client={queryClient}><I18nextProvider i18n={i18n}>{ui}</I18nextProvider></QueryClientProvider>)
}

beforeEach(() => {
  queryClient.clear()
  useAppStore.setState({ companyId: null, companyName: null, activeSprintId: null })
  vi.spyOn(api, 'capabilities').mockResolvedValue({ agent_graph: false, memory: false, github: false, auth_required: true })
  vi.spyOn(api, 'getAgentRuns').mockResolvedValue({ agent_runs: [] } as never)
  vi.spyOn(api, 'health').mockResolvedValue({ status: 'healthy', timestamp: '' })
  vi.spyOn(api, 'getOrchestrationHealth').mockResolvedValue({} as never)
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

describe('LanguageSwitcher', () => {
  it.each(['en', 'es', 'en-US', 'es-ES'])('shows the current language for %s and names the target', async language => {
    const i18n = createInstance()
    await i18n.use(initReactI18next).init({
      resources: { en: { translation: en }, es: { translation: es } },
      lng: language,
      fallbackLng: 'en',
    })
    render(<I18nextProvider i18n={i18n}><LanguageSwitcher /></I18nextProvider>)

    const spanish = language.startsWith('es')
    const button = screen.getByRole('button', {
      name: spanish ? 'Cambiar a inglés' : 'Switch to Spanish',
    })
    expect(button).toHaveTextContent(spanish ? 'ES' : 'EN')

    fireEvent.click(button)

    await waitFor(() => {
      expect(button).toHaveTextContent(spanish ? 'EN' : 'ES')
      expect(button).toHaveAccessibleName(spanish ? 'Switch to Spanish' : 'Cambiar a inglés')
    })
    expect(localStorage.getItem(STORAGE_KEY_LANG)).toBe(spanish ? 'en' : 'es')
    expect(i18n.resolvedLanguage).toBe(spanish ? 'en' : 'es')
  })
})

describe('unauthorized navigation', () => {
  it('does not nest the return destination when already on login (issue #1)', async () => {
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
    fireEvent.click(screen.getByRole('button', { name: 'Company 1' }))

    if (count < 2) {
      expect(screen.queryByRole('menuitem', { name: 'Switch company' })).not.toBeInTheDocument()
      return
    }

    fireEvent.click(screen.getByRole('menuitem', { name: 'Switch company' }))
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
      ['en', 'Evidence', 'Review', 'Sprints', 'Open menu', 'Mobile navigation'],
      ['es', 'Evidencia', 'Revisión', 'Sprints', 'Abrir menú', 'Navegación móvil'],
    ])('orders primary destinations in %s on desktop and mobile', async (lang, evidence, review, sprints, openMenu, mobileNav) => {
      i18n.addResourceBundle('es', 'translation', es)
      await i18n.changeLanguage(lang)
      authenticate()
      render(<App />)
      await waitFor(() => expect(window.location.pathname).toBe('/evidence'))
      const nav = screen.getByRole('navigation', { name: lang === 'es' ? 'Navegación principal' : 'Main navigation' })
      expect(within(nav).getAllByRole('link').map(link => link.textContent)).toEqual([evidence, review, sprints])
      expect(within(nav).getAllByRole('link').filter(link => link.getAttribute('aria-current') === 'page')).toHaveLength(1)
      expect(screen.getByRole('button', { name: 'Example company' })).toHaveAttribute('aria-haspopup', 'menu')
      expect(screen.getByRole('link', { name: /^La Fabrique/ })).toHaveAttribute('href', '/evidence')
      expect(screen.getByText(lang === 'es' ? 'En línea' : 'Online')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: openMenu }))
      expect(within(screen.getByRole('navigation', { name: mobileNav })).getAllByRole('link').map(link => link.textContent)).toEqual([evidence, review, sprints])
    })

    it.each([
      [false, false], [true, false], [false, true], [true, true],
    ])('shows optional links only for reported capabilities (graph %s, memory %s)', async (agent_graph, memory) => {
      authenticate()
      vi.mocked(api.capabilities).mockResolvedValue({ agent_graph, memory, github: false, auth_required: true })
      render(<App />)
      const nav = screen.getByRole('navigation', { name: 'Main navigation' })
      await waitFor(() => expect(api.capabilities).toHaveBeenCalled())
      await waitFor(() => {
        expect(Boolean(within(nav).queryByRole('link', { name: 'Generate' }))).toBe(agent_graph)
        expect(Boolean(within(nav).queryByRole('link', { name: 'Memory' }))).toBe(memory)
      })
    })

    it('hides optional links while capabilities load or fail', async () => {
      authenticate()
      let reject!: (reason: Error) => void
      vi.mocked(api.capabilities).mockImplementation(() => new Promise((_resolve, rejectPromise) => { reject = rejectPromise }))
      render(<App />)
      const nav = screen.getByRole('navigation', { name: 'Main navigation' })
      expect(within(nav).queryByRole('link', { name: 'Generate' })).not.toBeInTheDocument()
      expect(within(nav).queryByRole('link', { name: 'Memory' })).not.toBeInTheDocument()
      await act(async () => reject(new ApiError(404, 'not available')))
      await waitFor(() => expect(queryClient.getQueryState(['capabilities'])?.status).toBe('error'))
      expect(within(nav).queryByRole('link', { name: 'Generate' })).not.toBeInTheDocument()
      expect(within(nav).queryByRole('link', { name: 'Memory' })).not.toBeInTheDocument()
    })

    it('hides previously enabled optional links if a capabilities refresh fails', async () => {
      authenticate()
      vi.mocked(api.capabilities).mockResolvedValue({ agent_graph: true, memory: true, github: false, auth_required: true })
      render(<App />)
      const nav = screen.getByRole('navigation', { name: 'Main navigation' })
      expect(await within(nav).findByRole('link', { name: 'Generate' })).toBeInTheDocument()
      expect(within(nav).getByRole('link', { name: 'Memory' })).toBeInTheDocument()
      vi.mocked(api.capabilities).mockRejectedValue(new ApiError(404, 'not available'))
      await act(async () => { await queryClient.invalidateQueries({ queryKey: ['capabilities'] }) })
      await waitFor(() => {
        expect(within(nav).queryByRole('link', { name: 'Generate' })).not.toBeInTheDocument()
        expect(within(nav).queryByRole('link', { name: 'Memory' })).not.toBeInTheDocument()
      })
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
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
      const button = screen.getByRole('button', { name: 'Example company' })
      expect(button).toHaveAttribute('aria-expanded', 'false')
      fireEvent.click(button)
      expect(button).toHaveAttribute('aria-expanded', 'true')
      expect(screen.getByText('Example company')).toBeInTheDocument()
      expect(screen.getByRole('menuitem', { name: 'New company' })).toHaveAttribute('href', '/companies/new')
      fireEvent.click(screen.getByRole('menuitem', { name: 'Quotas' }))
      expect(await screen.findByText('Engineering')).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Token Quota Usage' })).toBeInTheDocument()
      expect(window.location.pathname).toBe('/company/quotas')
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
      expect(screen.queryByText('No agent runs awaiting review')).not.toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: 'Example company' }))
      fireEvent.click(screen.getByRole('menuitem', { name: 'Log out' }))
      await waitFor(() => expect(window.location.pathname).toBe('/login'))
      expect(useAuthStore.getState().token).toBeNull()
    })

    it('closes the Company dropdown with Escape', () => {
      authenticate()
      render(<App />)
      const button = screen.getByRole('button', { name: 'Example company' })
      fireEvent.click(button)
      fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' })
      expect(button).toHaveAttribute('aria-expanded', 'false')
      expect(button).toHaveFocus()
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    })

    it('lists Company actions for multiple memberships and closes on outside click', async () => {
      authenticate()
      useAuthStore.setState({ memberships: [
        { company_id: 1, human_role: 'Owner' },
        { company_id: 2, human_role: 'Viewer' },
      ] })
      window.history.replaceState(null, '', '/evidence')
      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Example company' }))
      expect(within(screen.getByRole('menu')).getAllByRole('menuitem').map(item => item.textContent))
        .toEqual(['Switch company', 'New company', 'Quotas', 'Log out'])
      fireEvent.pointerDown(document.body)
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    })

    it('uses the company placeholder when its name is unavailable', () => {
      authenticate()
      useAppStore.setState({ companyName: null })
      render(<App />)
      expect(screen.getByRole('button', { name: 'Company 1' })).toHaveAttribute('aria-haspopup', 'menu')
    })

    it.each([
      ['A user', 'person@example.invalid', 'A user'],
      ['', 'person@example.invalid', 'person@example.invalid'],
    ])('uses the user name/email without a company (%s)', (full_name, email, label) => {
      authenticate()
      useAppStore.setState({ companyId: null, companyName: null })
      useAuthStore.setState({ loadingMe: true, user: { full_name, email, user_id: 1 } })
      render(<App />)
      expect(screen.getByRole('button', { name: label })).toHaveAttribute('aria-haspopup', 'menu')
    })

    it('closes the mobile menu on Escape and restores focus to its labelled toggle', async () => {
      i18n.addResourceBundle('es', 'translation', es)
      await i18n.changeLanguage('es')
      authenticate()
      window.history.replaceState(null, '', '/evidence')
      render(<App />)
      const toggle = screen.getByRole('button', { name: 'Abrir menú' })
      expect(toggle).toHaveAttribute('aria-controls', 'mobile-menu')
      expect(toggle).toHaveAttribute('aria-expanded', 'false')
      fireEvent.click(toggle)
      expect(toggle).toHaveAccessibleName('Cerrar menú')
      const mobile = screen.getByRole('navigation', { name: 'Navegación móvil' })
      const link = within(mobile).getByRole('link', { name: 'Revisión' })
      link.focus()
      fireEvent.keyDown(link, { key: 'Escape' })
      expect(toggle).toHaveAttribute('aria-expanded', 'false')
      expect(toggle).toHaveAccessibleName('Abrir menú')
      expect(toggle).toHaveFocus()
      expect(screen.queryByRole('navigation', { name: 'Navegación móvil' })).not.toBeInTheDocument()
    })

    it('closes the mobile menu after selecting a destination', async () => {
      authenticate()
      window.history.replaceState(null, '', '/evidence')
      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Open menu' }))
      const mobile = screen.getByRole('navigation', { name: 'Mobile navigation' })
      fireEvent.click(within(mobile).getByRole('link', { name: 'Review' }))
      await waitFor(() => expect(window.location.pathname).toBe('/governance'))
      expect(screen.getByRole('button', { name: 'Open menu' })).toHaveAttribute('aria-expanded', 'false')
    })

    it.each([
      ['/evidence', 'Evidence'],
      ['/evidence/1', 'Evidence'],
      ['/governance', 'Review'],
      ['/dashboard', 'Sprints'],
      ['/dashboard/analytics', 'Sprints'],
    ])('has exactly one active primary destination at %s', (path, label) => {
      authenticate()
      vi.spyOn(api, 'getAgentRun').mockRejectedValue(new ApiError(404, 'not available'))
      vi.spyOn(api, 'getAgentRunVerifications').mockResolvedValue({ verifications: [] } as never)
      vi.spyOn(api, 'getAgentRunApprovals').mockResolvedValue({ approvals: [] } as never)
      window.history.replaceState(null, '', path)
      render(<App />)
      const primary = screen.getByRole('navigation', { name: 'Main navigation' })
      const active = within(primary).getAllByRole('link').filter(link => link.getAttribute('aria-current') === 'page')
      expect(active).toHaveLength(1)
      expect(active[0]).toHaveTextContent(label)
      expect(document.querySelectorAll('[aria-current="page"]')).toHaveLength(1)
      fireEvent.click(screen.getByRole('button', { name: 'Open menu' }))
      const mobile = screen.getByRole('navigation', { name: 'Mobile navigation' })
      const mobileActive = within(mobile).getAllByRole('link').filter(link => link.getAttribute('aria-current') === 'page')
      expect(mobileActive).toHaveLength(1)
      expect(mobileActive[0]).toHaveTextContent(label)
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
      const tabs = screen.getByRole('tablist', { name: 'Sprints' })
      expect(within(tabs).getByRole('tab', { name: 'Analytics' })).toHaveAttribute('aria-selected', 'true')
      expect(within(tabs).getByRole('tab', { name: 'Board' })).toHaveAttribute('aria-selected', 'false')
    })

    it('removes the duplicate Glassroom route', async () => {
      authenticate()
      window.history.replaceState(null, '', '/glassroom')
      render(<App />)
      expect(await screen.findByText('404')).toBeInTheDocument()
      expect(screen.queryByLabelText('Sprint ID')).not.toBeInTheDocument()
    })
  })

})
