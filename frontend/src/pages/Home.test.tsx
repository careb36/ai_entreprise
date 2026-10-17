import { render, screen } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider, initReactI18next } from 'react-i18next'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import en from '../i18n/en.json'
import es from '../i18n/es.json'
import { useAppStore } from '../store/useAppStore'
import Home from './Home'

async function renderHome() {
  const i18n = createInstance()
  await i18n.use(initReactI18next).init({
    resources: { en: { translation: en }, es: { translation: es } },
    lng: 'es',
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
  })
  return render(
    <I18nextProvider i18n={i18n}>
      <MemoryRouter><Home /></MemoryRouter>
    </I18nextProvider>,
  )
}

describe('Home in Spanish', () => {
  beforeEach(() => {
    useAppStore.setState({ companyId: null, companyName: null, activeSprintId: null })
  })

  it('translates the menu, links, descriptions and planning hint', async () => {
    await renderHome()

    expect(screen.getByRole('heading', { name: es.home.title })).toBeInTheDocument()
    for (const key of [
      'subtitle', 'companyDescription', 'boardDescription', 'agentsDescription',
      'generateDescription', 'planningHint',
    ] as const) {
      expect(screen.getByText(es.home[key])).toBeInTheDocument()
    }
    expect(screen.getByRole('link', { name: new RegExp(es.nav.newCompany) })).toHaveAttribute('href', '/companies/new')
    expect(screen.getByRole('link', { name: new RegExp(es.home.sprintBoard) })).toHaveAttribute('href', '/dashboard')
    expect(screen.getByRole('link', { name: new RegExp(es.nav.generate) })).toHaveAttribute('href', '/generate')
    expect(screen.getByRole('link', { name: es.home.openDashboard })).toHaveAttribute('href', '/dashboard')
    expect(screen.queryByText(en.home.subtitle)).not.toBeInTheDocument()
  })

  it.each([null, 42])('translates live execution with sprint %s', async sprintId => {
    useAppStore.setState({ companyId: 1, companyName: 'Acme', activeSprintId: sprintId })
    await renderHome()

    expect(screen.getByText(sprintId
      ? 'Ejecución en vivo para Acme · Sprint n.º 42'
      : 'Ejecución en vivo para Acme')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: new RegExp(es.nav.workspace) }))
      .toHaveAttribute('href', sprintId ? '/workspace/42' : '/companies/new')
  })
})
