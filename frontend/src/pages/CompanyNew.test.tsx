import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import es from '../i18n/es.json'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useMutation } from '@tanstack/react-query'
import CompanyNew from './CompanyNew'
import ProtectedRoute from '../components/ProtectedRoute'
import { api } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'
import { useAppStore } from '../store/useAppStore'

const modelCatalogue = vi.hoisted(() => ({
  models: [] as { id: string; provider: string; tier: string; description: string }[],
}))

vi.mock('../hooks/useQueries', () => ({
  useModels: () => ({ data: modelCatalogue }),
  useDiscoverTeam: () => useMutation({
    mutationFn: ({ companyId, projectPath }: { companyId: number | null; projectPath: string }) =>
      api.discoverTeam(companyId, projectPath),
  }),
  useInitCompany: () => useMutation({ mutationFn: api.initCompany }),
}))

function CurrentPath() {
  const location = useLocation()
  return <output data-testid="current-path">{location.pathname}</output>
}

describe('company creation profile refresh', () => {
  beforeEach(() => {
    modelCatalogue.models = []
    useAuthStore.getState().logout()
    useAuthStore.setState({
      token: 'valid-token',
      expiresAt: Date.now() + 60_000,
      meLoaded: true,
    })
  })

    async function renderSpanishWizard() {
      const i18n = createInstance()
      await i18n.init({
        lng: 'es',
        fallbackLng: false,
        resources: { es: { translation: es } },
        interpolation: { escapeValue: false },
      })
      const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      render(
        <I18nextProvider i18n={i18n}>
          <QueryClientProvider client={client}>
            <MemoryRouter>
              <CompanyNew />
            </MemoryRouter>
          </QueryClientProvider>
        </I18nextProvider>,
      )
    }

    function nextSpanishStep() {
      fireEvent.click(screen.getByRole('button', { name: 'Siguiente →' }))
    }

    describe('Spanish factory wizard', () => {
      afterEach(() => {
        vi.restoreAllMocks()
        vi.unstubAllGlobals()
      })

      it('localizes all six steps, validation, generated team defaults, options, and review', async () => {
        await renderSpanishWizard()
        expect(screen.getByRole('heading', { name: '🏭 Nueva Fábrica' })).toBeInTheDocument()
        for (const step of ['Proyecto', 'Empresa', 'Autonomía', 'Permisos', 'Equipo', 'Revisión']) {
          expect(screen.getByText(step)).toBeInTheDocument()
        }
        expect(screen.getByPlaceholderText(/ruta dentro del contenedor Docker/)).toBeInTheDocument()
        expect(screen.getByText('Aún no se ha analizado ningún proyecto.')).toBeInTheDocument()
        nextSpanishStep()
        expect(screen.getByRole('heading', { name: 'Detalles de la empresa' })).toBeInTheDocument()
        fireEvent.change(screen.getByPlaceholderText('Acme Corp'), { target: { value: 'A' } })
        expect(screen.getByText('El nombre debe tener al menos 2 caracteres.')).toBeInTheDocument()
        expect(screen.getByRole('button', { name: 'Siguiente →' })).toBeDisabled()
        fireEvent.change(screen.getByPlaceholderText('Acme Corp'), { target: { value: 'Mi Fábrica' } })
        expect(screen.getByPlaceholderText('Una breve descripción de lo que construye esta fábrica…')).toBeInTheDocument()
        expect(screen.getByRole('option', { name: 'Mediano — hasta 8 tareas, una funcionalidad completa' })).toHaveValue('M')
        expect(screen.getByRole('option', { name: 'Nueva York' })).toHaveValue('America/New_York')
        nextSpanishStep()
        expect(screen.getByText('Nivel 2 — Gestor de excepciones')).toBeInTheDocument()
        fireEvent.click(screen.getByRole('button', { name: /Nivel 1 — Copiloto/ }))
        expect(screen.getByText('Supervisión humana completa')).toBeInTheDocument()
        nextSpanishStep()
        expect(screen.getByText('Git / Control de versiones')).toBeInTheDocument()
        expect(screen.getAllByRole('option', { name: 'Solo lectura' })[0]).toHaveValue('read_only')
        expect(screen.getByText('Requerir aprobación humana antes de cualquier despliegue')).toBeInTheDocument()
        expect(screen.getByText(/En el nivel de autonomía 1/)).toBeInTheDocument()
        nextSpanishStep()
        expect(screen.getByRole('heading', { name: 'Crea tu equipo de IA' })).toBeInTheDocument()
        expect(screen.getByDisplayValue('Gestión de producto y trabajo pendiente')).toBeInTheDocument()
        expect(screen.getByDisplayValue('Pruebas automatizadas')).toBeInTheDocument()
        expect(screen.getAllByRole('option', { name: '💻 Desarrollador' })[0]).toHaveValue('Dev')
        expect(screen.getAllByRole('option', { name: 'Intermedio (2–5 años)' })[0]).toHaveValue('Mid')
        expect(screen.getAllByText('Cargando modelos…')).toHaveLength(4)
        expect(screen.getAllByTitle('Eliminar')).toHaveLength(4)
        expect(screen.getByText('2.3 M de tokens')).toBeInTheDocument()
        fireEvent.click(screen.getByRole('button', { name: '+ Añadir agente' }))
        expect(screen.getAllByTitle('Eliminar')).toHaveLength(5)
        nextSpanishStep()
        expect(screen.getByRole('heading', { name: 'Revisar e inicializar' })).toBeInTheDocument()
        expect(screen.getByText('Equipo — 5 agentes')).toBeInTheDocument()
        expect(screen.getByText(/Sprints de alcance M/)).toBeInTheDocument()
        expect(screen.getByText('Nivel 1')).toBeInTheDocument()
        expect(screen.getByText('2.8 M de tokens/mes')).toBeInTheDocument()
        expect(screen.getAllByRole('button', { name: 'Editar' })).toHaveLength(4)
        expect(screen.getByRole('button', { name: '🏭 Inicializar Fábrica' })).toBeInTheDocument()
        expect(document.body.textContent).not.toMatch(/companyNew\./)
      })

      it('localizes empty directory and directory network error alerts', async () => {
        const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
        const fetch = vi.fn()
          .mockResolvedValueOnce({ json: async () => ({ directories: [], root: '/projects' }) })
          .mockRejectedValueOnce(new Error('Sin conexión'))
        vi.stubGlobal('fetch', fetch)
        await renderSpanishWizard()
        fireEvent.click(screen.getByRole('button', { name: '📂 Examinar' }))
        await waitFor(() => expect(alert).toHaveBeenCalledWith('No se encontraron directorios en /projects'))
        fireEvent.click(screen.getByRole('button', { name: '📂 Examinar' }))
        await waitFor(() => expect(alert).toHaveBeenCalledWith('Error al listar directorios: Sin conexión'))
      })

      it('localizes the directory picker close control without altering directory names', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
          json: async () => ({ directories: [{ name: 'repository-name', path: '/projects/repository-name' }] }),
        }))
        await renderSpanishWizard()
        fireEvent.click(screen.getByRole('button', { name: '📂 Examinar' }))
        expect(await screen.findByRole('button', { name: '📁 repository-name' })).toBeInTheDocument()
        fireEvent.click(screen.getByRole('button', { name: '✕ Cerrar' }))
        expect(screen.queryByRole('button', { name: '✕ Cerrar' })).not.toBeInTheDocument()
      })

      it('localizes scanning and discovery errors', async () => {
        let rejectDiscovery!: (error: Error) => void
        vi.spyOn(api, 'discoverTeam').mockImplementation(() => new Promise((_, reject) => { rejectDiscovery = reject }))
        await renderSpanishWizard()
        fireEvent.change(screen.getByPlaceholderText(/ruta dentro del contenedor Docker/), { target: { value: '/projects/demo' } })
        fireEvent.click(screen.getByRole('button', { name: '🔍 Analizar' }))
        expect(await screen.findByText('Analizando…')).toBeInTheDocument()
        await act(async () => { rejectDiscovery(new Error('Repositorio inaccesible')) })
        expect(await screen.findByText('Error al analizar el proyecto: Repositorio inaccesible')).toBeInTheDocument()
      })

      it.each(['ai', 'rules'] as const)('localizes detected stack metadata and %s team suggestions', async teamSource => {
        vi.spyOn(api, 'discoverTeam').mockResolvedValue({
          discovery_id: 1,
          detected_stack: {
            name: 'demo', language: 'TypeScript', frameworks: ['React'], databases: [], infra: [],
            complexity: 'High', file_count: 1, team_source: teamSource,
          },
          suggested_team: [{
            role: 'Dev', seniority: 'Mid', specialty: 'TypeScript',
            model: 'legacy-model-id', system_prompt: 'User-provided prompt',
          }],
        })
        await renderSpanishWizard()
        fireEvent.change(screen.getByPlaceholderText(/ruta dentro del contenedor Docker/), { target: { value: '/projects/demo' } })
        fireEvent.click(screen.getByRole('button', { name: '🔍 Analizar' }))
        expect(await screen.findByText(/Tecnologías detectadas/)).toBeInTheDocument()
        expect(screen.getByText('Lenguaje principal')).toBeInTheDocument()
        expect(screen.getByText('1 archivo analizado')).toBeInTheDocument()
        expect(screen.getByText('Complejidad alta')).toBeInTheDocument()
        expect(screen.getByText(teamSource === 'ai' ? /Equipo sugerido por IA/ : 'Sugerencia basada en reglas')).toBeInTheDocument()
        for (let step = 0; step < 4; step++) nextSpanishStep()
        expect(screen.getByText('Equipo sugerido según las tecnologías detectadas. Personalízalo según tus necesidades.')).toBeInTheDocument()
        expect(screen.getByRole('option', { name: 'legacy-model-id (sin resolver)' })).toHaveValue('legacy-model-id')
        fireEvent.click(screen.getByRole('button', { name: /Instrucciones del sistema/ }))
        expect(screen.getByDisplayValue('User-provided prompt')).toBeInTheDocument()
      })

      it('localizes model tiers while preserving model identifiers and descriptions', async () => {
        modelCatalogue.models = [
          { id: 'model-id', provider: 'gemini', tier: 'strategic', description: 'Catalogue-provided description' },
        ]
        await renderSpanishWizard()
        nextSpanishStep()
        fireEvent.change(screen.getByPlaceholderText('Acme Corp'), { target: { value: 'Mi Fábrica' } })
        for (let step = 1; step < 4; step++) nextSpanishStep()
        expect(screen.getAllByRole('group', { name: '🔵 Estratégico' })).toHaveLength(4)
        expect(screen.getAllByRole('option', { name: 'model-id (gemini)' })[0]).toHaveValue('model-id')
        fireEvent.change(screen.getAllByRole('combobox')[2], { target: { value: 'model-id' } })
        expect(screen.getByText('Catalogue-provided description')).toBeInTheDocument()
      })

      it.each([
        { message: '', expected: 'Error desconocido' },
        { message: 'Servicio no disponible', expected: 'Servicio no disponible' },
      ])('localizes initialization failures and preserves API identifiers ($expected)', async ({ message, expected }) => {
        const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
        vi.spyOn(console, 'error').mockImplementation(() => {})
        const init = vi.spyOn(api, 'initCompany').mockRejectedValue(new Error(message))
        await renderSpanishWizard()
        nextSpanishStep()
        fireEvent.change(screen.getByPlaceholderText('Acme Corp'), { target: { value: 'Mi Fábrica' } })
        for (let step = 1; step < 5; step++) nextSpanishStep()
        fireEvent.click(screen.getByRole('button', { name: '🏭 Inicializar Fábrica' }))
        await waitFor(() => expect(alert).toHaveBeenCalledWith(`Error al inicializar la fábrica: ${expected}`))
        if (message) expect(await screen.findByText(`Error al inicializar la fábrica: ${message}`)).toBeInTheDocument()
        expect(init.mock.calls[0][0]).toEqual(expect.objectContaining({
          company_name: 'Mi Fábrica', default_scope_size: 'M', timezone: 'UTC',
          permissions: expect.objectContaining({ git_access: 'read_only', cloud_access: 'none' }),
          agents: expect.arrayContaining([expect.objectContaining({ role: 'Dev', seniority: 'Mid' })]),
        }))
      })
    })

  afterEach(() => vi.restoreAllMocks())

  it.each([
    { scenario: 'first company', memberships: [] },
    { scenario: 'second company', memberships: [{ company_id: 7, human_role: 'Owner' as const }] },
  ])('keeps the created company and workspace when refresh fails ($scenario; issue #6)', async ({ memberships }) => {
    useAuthStore.setState({ memberships })
    if (memberships.length) useAppStore.getState().setCompany(7, 'Existing Factory')
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } })
    const init = vi.spyOn(api, 'initCompany').mockResolvedValue({
      company_id: 14, sprint_id: 5, task_count: 0, message: 'Created',
    })
    let rejectProfile!: (error: Error) => void
    const me = vi.spyOn(api, 'me')
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectProfile = reject }))
      .mockResolvedValue({
        user_id: 1, email: 'user@example.com', full_name: 'User',
        memberships: [...memberships, { company_id: 14, human_role: 'Owner' }],
      })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/companies/new']}>
          <CurrentPath />
          <Routes>
            <Route element={<ProtectedRoute />}>
              <Route path="/companies/new" element={<CompanyNew />} />
              <Route path="/workspace/5" element={<div>Created workspace</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Next →' }))
    fireEvent.change(screen.getByPlaceholderText('Acme Corp'), { target: { value: 'New Factory' } })
    for (let step = 1; step < 5; step++) {
      fireEvent.click(screen.getByRole('button', { name: 'Next →' }))
    }
    fireEvent.click(screen.getByRole('button', { name: /Initialize Factory/ }))
    expect(await screen.findByRole('status')).toBeInTheDocument()
    expect(me).toHaveBeenCalledOnce()
    expect(useAppStore.getState().companyId).toBe(14)

    await act(async () => { rejectProfile(new Error('Profile unavailable')) })

    expect(screen.getByRole('alert')).toHaveTextContent('Profile unavailable')
    expect(useAppStore.getState()).toMatchObject({ companyId: 14, companyName: 'New Factory' })
    expect(screen.getByTestId('current-path')).toHaveTextContent('/workspace/5')

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('Created workspace')).toBeInTheDocument()
    expect(init).toHaveBeenCalledOnce()
    client.clear()
  })
})
