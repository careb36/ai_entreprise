import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import App, { LanguageSwitcher } from './App'
import en from './i18n/en.json'
import es from './i18n/es.json'
import { STORAGE_KEY_LANG } from './lib/constants'
import { api, ApiError, setOnUnauthorized } from './services/api'
import { useAuthStore } from './store/useAuthStore'
import { useAppStore } from './store/useAppStore'

afterEach(() => {
  setOnUnauthorized(null)
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  useAuthStore.getState().logout()
  window.history.replaceState(null, '', '/')
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

    if (count < 2) {
      expect(screen.queryByRole('link', { name: 'Switch company' })).not.toBeInTheDocument()
      return
    }

    fireEvent.click(screen.getByRole('link', { name: 'Switch company' }))
    expect(await screen.findByRole('heading', { name: 'Select a company' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Company 2\s*Owner/ }))
    await waitFor(() => expect(window.location.pathname).toBe('/'))
    expect(useAppStore.getState().companyId).toBe(2)
    expect(useAppStore.getState().activeSprintId).toBeNull()
  })

  it('does not emit React Router future-flag warnings', () => {
    useAuthStore.getState().logout()
    window.history.replaceState(null, '', '/login')
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    render(<App />)

    expect(warn.mock.calls.flat().join(' ')).not.toMatch(/React Router Future Flag Warning/)
  })
})
