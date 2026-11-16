// The app shell: the language switcher, unauthorized navigation, company
// switching, the desktop app's menu, the section of a path and the service
// status. The evidence-first navigation is in AppNavigation.test.tsx; what
// they share is in test/appFixtures.tsx.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import App, { LanguageSwitcher, sectionForPath } from './App'
import en from './i18n/en.json'
import { STORAGE_KEY_LANG } from './lib/constants'
import { api, ApiError } from './services/api'
import { useAuthStore } from './store/useAuthStore'
import { useAppStore } from './store/useAppStore'
import { queryClient } from './lib/queryClient'
import es from './i18n/es.json'
import { authenticate, installAppTestHooks, render } from './test/appFixtures'

installAppTestHooks()

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
      useAuthStore.setState({ expiresAt: Date.now() + 60_000 })
      await expect(api.get('/v1/protected')).rejects.toBeInstanceOf(ApiError)
    })

    expect(useAuthStore.getState().expiresAt).toBeNull()
    expect(window.location.pathname).toBe('/login')
    expect(window.location.search).toBe('?from=%2Fevidence%2F5')
  })
})

describe('app navigation', () => {
  it.each([0, 1, 2])('offers company switching only for multiple memberships (%i)', async count => {
    useAuthStore.setState({
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

  describe('desktop app menu', () => {
    afterEach(() => { delete window.electronAPI })

    it('navigates when the native Factory menu asks to, and unsubscribes on unmount', async () => {
      authenticate()
      let menuNavigate: ((path: string) => void) | null = null
      const unsubscribe = vi.fn()
      window.electronAPI = {
        platform: 'linux',
        versions: { node: '', chrome: '', electron: '' },
        selectDirectory: async () => null,
        onNavigate: callback => {
          menuNavigate = callback
          return unsubscribe
        },
        getBackendUrl: async () => '',
        getAppVersion: async () => '',
        openExternal: async () => {},
      }
      window.history.replaceState(null, '', '/evidence')
      const { unmount } = render(<App />)
      expect(menuNavigate).not.toBeNull()

      act(() => menuNavigate!('/sprints'))
      await waitFor(() => expect(window.location.pathname).toBe('/sprints'))
      act(() => menuNavigate!('https://example.com/elsewhere'))
      expect(window.location.pathname).toBe('/sprints')

      unmount()
      expect(unsubscribe).toHaveBeenCalled()
    })
  })
})

describe('sectionForPath', () => {
  it.each([
    ['/lineage', 'evidence'],
    ['/company/members', 'company'],
    ['/company/github', 'company'],
    ['/support', 'company'],
    ['/supported', null],
  ])('puts %s under %s', (path, section) => {
    expect(sectionForPath(path)).toBe(section)
  })
})

describe('service status', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    queryClient.clear()
  })

  it('checks each service only when the status pill is opened', async () => {
    useAuthStore.setState({
      expiresAt: Date.now() + 60_000, meLoaded: true, loadingMe: false,
      memberships: [{ company_id: 1, human_role: 'Owner' }],
    })
    useAppStore.getState().setCompany(1, 'Example company')
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'healthy', timestamp: '' })
    const deep = vi.spyOn(api, 'deepHealth').mockResolvedValue({
      status: 'degraded',
      components: {
        postgres: { status: 'up' },
        nats: { status: 'up' },
        llm_proxy: { status: 'up' },
        neo4j: { status: 'down', reason: 'ServiceUnavailable: no route' },
      },
    })
    window.history.replaceState(null, '', '/evidence')
    render(<App />)

    const pill = await screen.findByRole('button', { name: 'Online' })
    expect(deep).not.toHaveBeenCalled()
    fireEvent.click(pill)

    const dialog = await screen.findByRole('dialog', { name: 'Service status' })
    expect(await within(dialog).findByText('Degraded: an optional service is down.')).toBeInTheDocument()
    const neo4j = within(dialog).getByText('Knowledge graph (Neo4j)').closest('li')!
    expect(neo4j).toHaveTextContent('down')
    expect(neo4j).toHaveTextContent('ServiceUnavailable: no route')
    expect(within(dialog).getByText('Database (Postgres)').closest('li')).toHaveTextContent('up')
    expect(deep).toHaveBeenCalledTimes(1)

    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('dialog', { name: 'Service status' })).not.toBeInTheDocument()
  })

  it('shows a knowledge graph switched off as off, not down', async () => {
    useAuthStore.setState({
      expiresAt: Date.now() + 60_000, meLoaded: true, loadingMe: false,
      memberships: [{ company_id: 1, human_role: 'Owner' }],
    })
    useAppStore.getState().setCompany(1, 'Example company')
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'healthy', timestamp: '' })
    vi.spyOn(api, 'deepHealth').mockResolvedValue({
      status: 'healthy',
      components: {
        postgres: { status: 'up' },
        nats: { status: 'up' },
        llm_proxy: { status: 'up' },
        neo4j: { status: 'disabled', reason: 'NEO4J_ENABLED=false' },
      },
    })
    window.history.replaceState(null, '', '/evidence')
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: 'Online' }))

    const dialog = await screen.findByRole('dialog', { name: 'Service status' })
    expect(await within(dialog).findByText('All services are up.')).toBeInTheDocument()
    const neo4j = within(dialog).getByText('Knowledge graph (Neo4j)').closest('li')!
    expect(neo4j).toHaveTextContent('off')
    expect(neo4j).not.toHaveTextContent('down')
    expect(neo4j).not.toHaveTextContent('NEO4J_ENABLED')
  })
})
