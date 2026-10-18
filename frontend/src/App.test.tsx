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
  return rtlRender(<I18nextProvider i18n={i18n}><QueryClientProvider client={queryClient}>{ui}</QueryClientProvider></I18nextProvider>)
}

beforeEach(() => {
  queryClient.clear()
  useAppStore.setState({ companyId: null, companyName: null, activeSprintId: null })
  vi.spyOn(api, 'get').mockResolvedValue({})
  vi.spyOn(api, 'capabilities').mockResolvedValue({ agent_graph: false, memory: false, github: false, auth_required: true })
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
      expect(screen.getByRole('button', { name: 'Example company' })).toBeInTheDocument()
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
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: 'Example company' }))
      expect(screen.getByText('Example company')).toBeInTheDocument()
      expect(screen.getByRole('menuitem', { name: 'New company' })).toHaveAttribute('href', '/companies/new')
      expect(screen.getAllByRole('menuitem')).toHaveLength(3)
      fireEvent.click(screen.getByRole('menuitem', { name: 'Quotas' }))
      expect(await screen.findByText('Engineering')).toBeInTheDocument()
      expect(window.location.pathname).toBe('/company/quotas')
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Token Quota Usage' })).toBeInTheDocument()
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
      const item = screen.getByRole('menuitem', { name: 'Quotas' })
      item.focus()
      fireEvent.keyDown(item, { key: 'Escape' })
      expect(button).toHaveAttribute('aria-expanded', 'false')
      expect(button).toHaveFocus()
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    })

    it('closes the Company dropdown on an outside click', () => {
      authenticate()
      render(<App />)
      fireEvent.click(screen.getByRole('button', { name: 'Example company' }))
      fireEvent.mouseDown(document.body)
      expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    })

    it('falls back to a localized company ID when its name is unavailable', async () => {
      i18n.addResourceBundle('es', 'translation', es)
      await i18n.changeLanguage('es')
      authenticate()
      useAppStore.setState({ companyName: null })
      render(<App />)
      expect(screen.getByRole('button', { name: 'Empresa 1' })).toHaveAttribute('aria-haspopup', 'menu')
    })

    it('lists all four Company actions for multiple memberships', () => {
      authenticate()
      useAuthStore.setState({ memberships: [
        { company_id: 1, human_role: 'Owner' },
        { company_id: 2, human_role: 'Viewer' },
      ] })
      render(<App />)
      const button = screen.getByRole('button', { name: 'Example company' })
      expect(button).toHaveAttribute('aria-haspopup', 'menu')
      fireEvent.click(button)
      expect(within(screen.getByRole('menu')).getAllByRole('menuitem').map(item => item.textContent))
        .toEqual(['Switch company', 'New company', 'Quotas', 'Log out'])
    })

    it('closes mobile navigation with Escape and restores toggle focus', () => {
      authenticate()
      render(<App />)
      const button = screen.getByRole('button', { name: 'Open menu' })
      expect(button).toHaveAttribute('aria-controls', 'mobile-navigation')
      fireEvent.click(button)
      const nav = screen.getByRole('navigation', { name: 'Mobile navigation' })
      const link = within(nav).getByRole('link', { name: 'Review' })
      link.focus()
      fireEvent.keyDown(link, { key: 'Escape' })
      expect(button).toHaveAttribute('aria-expanded', 'false')
      expect(button).toHaveFocus()
      expect(screen.queryByRole('navigation', { name: 'Mobile navigation' })).not.toBeInTheDocument()
    })

    it.each(['/evidence', '/governance', '/dashboard', '/dashboard/analytics'])('marks only one primary destination current at %s', async path => {
      authenticate()
      window.history.replaceState(null, '', path)
      vi.spyOn(api, 'getOrchestrationHealth').mockResolvedValue({} as never)
      render(<App />)
      await waitFor(() => expect(document.querySelectorAll('[aria-current="page"]')).toHaveLength(1))
      expect(screen.getByRole('navigation', { name: 'Main navigation' }).querySelector('[aria-current="page"]'))
        .toHaveAttribute('href', path.startsWith('/dashboard') ? '/dashboard' : path)
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
