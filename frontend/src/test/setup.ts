import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import { afterEach, beforeAll } from 'vitest'
import en from '../i18n/en.json'

beforeAll(async () => {
  // No backend answers in tests. Without this, a request still in flight when a
  // test's own fetch stub is removed reaches Node's real fetch, fails on
  // localhost:8000 as a network error and is retried by the API client 600 ms
  // later, inside the next test. A 404 is final: nothing is retried.
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: 'No backend in tests' }), { status: 404, headers: { 'Content-Type': 'application/json' } })
  if (!i18n.isInitialized) {
    i18n.use(initReactI18next)
    await i18n.init({
      resources: {
        en: {
          translation: en,
        },
      },
      lng: 'en',
      fallbackLng: 'en',
      interpolation: {
        escapeValue: false,
      },
    })
  }
})

afterEach(() => {
  cleanup()
  // Node-environment tests (e.g. the Vite config test) have no localStorage.
  if (typeof localStorage !== 'undefined') localStorage.clear()
})
