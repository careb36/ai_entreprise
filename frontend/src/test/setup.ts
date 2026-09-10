import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import { afterEach } from 'vitest'
import en from '../i18n/en.json'

if (!i18n.isInitialized) {
  void i18n.use(initReactI18next).init({
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
    initImmediate: false,
  })
}

afterEach(() => {
  cleanup()
  localStorage.clear()
})
