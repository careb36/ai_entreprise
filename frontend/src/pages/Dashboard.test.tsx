import { fireEvent, render, screen } from '@testing-library/react'
import i18n, { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Dashboard from './Dashboard'
import {
  useAnalyticsSummary, useCapabilities, useInitCompany, usePlanSelfImprovementSprint,
  usePlanSprint, useQuotas, useSprintBoard, useStartSprint,
} from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'
import { useAgentStore } from '../store/agentStore'
import type { SprintPlanResponse } from '../services/api'
import es from '../i18n/es.json'

vi.mock('../hooks/useQueries', () => ({
  useAnalyticsSummary: vi.fn(),
  useCapabilities: vi.fn(),
  useInitCompany: vi.fn(),
  usePlanSelfImprovementSprint: vi.fn(),
  usePlanSprint: vi.fn(),
  useSprintBoard: vi.fn(),
  useStartSprint: vi.fn(),
  useQuotas: vi.fn(() => ({ data: [] })),
}))

const plan: SprintPlanResponse = {
  sprint_name: 'External sprint name',
  goal: 'External sprint goal',
  scope_size: 'M',
  tasks: [{
    title: 'External task title',
    description: 'External task description',
    assignee: 'Developer',
    priority: 'high',
  }, {
    title: 'Custom producer task',
    description: 'External producer description',
    assignee: 'custom-agent',
    priority: 'low',
  }],
}

function mutation(overrides: Record<string, unknown> = {}) {
  return { isPending: false, error: null, reset: vi.fn(), mutateAsync: vi.fn(), ...overrides }
}

function renderDashboard(sprintId: number | null = null) {
  return render(
    <MemoryRouter>
      <Routes>
        <Route path="/" element={<Dashboard sprintId={sprintId} />} />
        <Route path="/sprints/23/board" element={<div data-testid="workspace" />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Dashboard Spanish localization', () => {
  beforeEach(async () => {
    vi.clearAllMocks()
    i18n.addResourceBundle('es', 'translation', es, true, true)
    await i18n.changeLanguage('es')
    useAppStore.setState({ companyId: 42, companyName: null, activeSprintId: null })
    useAgentStore.setState({
      agentStates: {},
      isConnected: false,
      connect: vi.fn(),
      disconnect: vi.fn(),
    })
    vi.mocked(useSprintBoard).mockReturnValue({ data: undefined, isLoading: false } as never)
    vi.mocked(useAnalyticsSummary).mockReturnValue({ data: undefined } as never)
    vi.mocked(useCapabilities).mockReturnValue({ data: { agent_graph: true, memory: true }, isError: false, isPending: false } as never)
    vi.mocked(useInitCompany).mockReturnValue(mutation() as never)
    vi.mocked(usePlanSprint).mockReturnValue(mutation({ mutateAsync: vi.fn().mockResolvedValue(plan) }) as never)
    vi.mocked(usePlanSelfImprovementSprint).mockReturnValue(mutation({ mutateAsync: vi.fn().mockResolvedValue(plan) }) as never)
    vi.mocked(useStartSprint).mockReturnValue(mutation({ mutateAsync: vi.fn().mockResolvedValue({ sprint_id: 23 }) }) as never)
    vi.mocked(useQuotas).mockReturnValue({ data: [] } as never)
  })

  it('renders Spanish empty-state, KPI, scope and stream labels without repeating the shell', () => {
    renderDashboard()

    expect(screen.getByText('Centro de mando de sprints con IA')).toBeInTheDocument()
    expect(screen.getAllByText('No hay sprint activo')).toHaveLength(2)
    expect(screen.getByText('Aún no se han visto agentes.')).toBeInTheDocument()
    expect(screen.getByText('Tareas del sprint')).toBeInTheDocument()
    expect(screen.getByText('Agentes trabajando')).toBeInTheDocument()
    expect(screen.getByText('Puntuación de salud')).toBeInTheDocument()
    expect(screen.getByText('Aún no hay datos')).toBeInTheDocument()
    expect(screen.getByText('Mediano · hasta 8 tareas')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'P' })).toHaveAttribute('title', 'Pequeño')
    expect(screen.getByRole('button', { name: 'G' })).toHaveAttribute('title', 'Grande')
    expect(screen.getByText('Flujo de agentes')).toBeInTheDocument()
    // The shell already reports backend health and owns company creation.
    expect(screen.queryByText('API del backend')).not.toBeInTheDocument()
    expect(screen.queryByText('Sistema en línea')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Nueva Fábrica' })).not.toBeInTheDocument()
    expect(screen.queryByText('Launch Sprint')).not.toBeInTheDocument()
    expect(screen.queryByText(/dashboard\./)).not.toBeInTheDocument()
  })

  it.each([
    { data: undefined, isError: false },
    { data: { agent_graph: true, memory: true }, isError: false },
    { data: { agent_graph: false, memory: false }, isError: false },
  ])('leaves navigation shortcuts to the shell and links only to agent management (%j)', capabilities => {
    vi.mocked(useCapabilities).mockReturnValue(capabilities as never)
    renderDashboard()
    expect(screen.getAllByRole('link').map(link => link.getAttribute('href'))).toEqual(['/agents'])
    expect(screen.getByRole('link', { name: 'Gestionar agentes' })).toHaveAttribute('href', '/agents')
  })

  it('translates the quota alert and links it to quotas', () => {
    vi.mocked(useQuotas).mockReturnValue({ data: [{ id: 1, unit: 'Engineering', used: 90, limit: 100 }] } as never)
    renderDashboard()
    expect(screen.getByText(/La cuota de tokens supera el 80/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Gestionar cuotas' })).toHaveAttribute('href', '/company/quotas')
    expect(screen.queryByText('Manage quotas')).not.toBeInTheDocument()
  })

  it('localizes populated summaries, split status and roles while preserving external data', () => {
    useAppStore.setState({ companyName: 'External company' })
    vi.mocked(useSprintBoard).mockReturnValue({
      data: {
        sprint: { sprint_name: 'External sprint', goal: 'External goal', status: 'needs_split', scope_size: 'S' },
        columns: { done: [{}], blocked: [{}], in_progress: [{}] },
        total: 4,
      },
      isLoading: false,
    } as never)
    vi.mocked(useAnalyticsSummary).mockReturnValue({ data: { health_score: 55 } } as never)
    useAgentStore.setState({
      isConnected: true,
      agentStates: {
        Developer: { role: 'Developer', fsmState: 'active', currentActivity: '', color: '', lastSeen: '' },
        'custom-agent': { role: 'custom-agent', fsmState: 'idle', currentActivity: 'External activity', color: '', lastSeen: '' },
      },
    })
    renderDashboard(9)

    expect(screen.getByRole('heading', { name: 'External company' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'External sprint' })).toBeInTheDocument()
    expect(screen.getByText('External goal')).toBeInTheDocument()
    expect(screen.getByText('Requiere división')).toBeInTheDocument()
    expect(screen.getByText('Pequeño')).toBeInTheDocument()
    expect(screen.getByText('1 de 4 tareas completadas')).toBeInTheDocument()
    expect(screen.getByText('25% completado')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Salud del sprint' })).toBeInTheDocument()
    expect(screen.getByText('1/4 tareas')).toBeInTheDocument()
    expect(screen.getByTitle('Hecho: 25%')).toBeInTheDocument()
    expect(screen.getByTitle('En progreso: 25%')).toBeInTheDocument()
    expect(screen.getByTitle('Bloqueado: 25%')).toBeInTheDocument()
    expect(screen.getByTitle('Restante: 25%')).toBeInTheDocument()
    expect(screen.getByText('Necesita atención')).toBeInTheDocument()
    expect(screen.getByText('Revisión necesaria')).toBeInTheDocument()
    expect(screen.getByText('2 agentes')).toBeInTheDocument()
    expect(screen.getByText('Desarrollador')).toBeInTheDocument()
    expect(screen.getByText('inactivo')).toBeInTheDocument()
    expect(screen.getByText('custom-agent')).toBeInTheDocument()
    expect(screen.getByText('External activity')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Revisar' })).toHaveAttribute('href', '/sprints/9/live')
    expect(screen.getByRole('link', { name: 'Gestionar agentes' })).toHaveAttribute('href', '/agents')
    expect(screen.queryByRole('link', { name: 'Vista en vivo' })).not.toBeInTheDocument()
  })

  it.each([
    ['active', 'Activo', 'M', 'Mediano'],
    ['completed', 'Completado', 'L', 'Grande'],
    ['cancelled', 'Cancelado', 'S', 'Pequeño'],
  ])('localizes %s sprint metadata', (status, statusLabel, scope, scopeLabel) => {
    vi.mocked(useSprintBoard).mockReturnValue({
      data: { sprint: { sprint_name: 'External sprint', status, scope_size: scope }, total: 0, columns: {} },
      isLoading: false,
    } as never)
    renderDashboard(9)
    expect(screen.getByText(statusLabel)).toBeInTheDocument()
    expect(screen.getByText(scopeLabel)).toBeInTheDocument()
  })

  it.each([
    ['en', 'No sprint data available', 'No data yet'],
    ['es', 'No hay datos del sprint disponibles', 'Aún no hay datos'],
  ])('keeps the chart empty state separate from the health KPI in %s', async (language, chartEmpty, healthEmpty) => {
    await i18n.changeLanguage(language)
    vi.mocked(useSprintBoard).mockReturnValue({
      data: { sprint: { sprint_name: 'External sprint', status: 'active', scope_size: 'M' }, total: 0, columns: {} },
      isLoading: false,
    } as never)
    renderDashboard(9)
    expect(screen.getByText(chartEmpty)).toBeInTheDocument()
    expect(screen.getByText(healthEmpty)).toBeInTheDocument()
  })

  it('generates and launches a Spanish preview without translating API identifiers or freeform plan data', async () => {
    const planMutation = mutation({ mutateAsync: vi.fn().mockResolvedValue(plan) })
    const startMutation = mutation({ mutateAsync: vi.fn().mockResolvedValue({ sprint_id: 23 }) })
    vi.mocked(usePlanSprint).mockReturnValue(planMutation as never)
    vi.mocked(useStartSprint).mockReturnValue(startMutation as never)
    renderDashboard()

    fireEvent.change(screen.getByPlaceholderText('Describe el objetivo del sprint…'), { target: { value: 'User goal' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generar plan' }))

    expect(await screen.findByText('Vista previa del plan')).toBeInTheDocument()
    expect(screen.getByText('External task description')).toBeInTheDocument()
    expect(screen.getByText('Desarrollador')).toBeInTheDocument()
    expect(screen.getByText('Alta')).toBeInTheDocument()
    expect(screen.getByText('custom-agent')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Regenerar' })).toBeInTheDocument()
    expect(screen.getByText('Revisa el plan, regénéralo si es necesario y después inicia el sprint.')).toBeInTheDocument()
    expect(planMutation.mutateAsync).toHaveBeenCalledWith({ company_id: 42, prompt: 'User goal', scope_size: 'M' })

    fireEvent.click(screen.getByRole('button', { name: 'Iniciar sprint' }))
    expect(await screen.findByTestId('workspace')).toBeInTheDocument()
    expect(startMutation.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({
      company_id: 42,
      sprint_name: plan.sprint_name,
      goal: plan.goal,
      scope_size: 'M',
      backlog_items: plan.tasks,
    }))
    expect(useAppStore.getState().activeSprintId).toBe(23)
  })

  it('shows the autonomous planning preview and localized demo company fallback', async () => {
    useAppStore.setState({ companyId: null })
    const initMutation = mutation({ mutateAsync: vi.fn().mockResolvedValue({ company_id: 77 }) })
    const autoMutation = mutation({ mutateAsync: vi.fn().mockResolvedValue(plan) })
    vi.mocked(useInitCompany).mockReturnValue(initMutation as never)
    vi.mocked(usePlanSelfImprovementSprint).mockReturnValue(autoMutation as never)
    renderDashboard()

    fireEvent.click(screen.getByRole('button', { name: 'Mejorar automáticamente' }))
    expect(await screen.findByText('Vista previa del plan autónomo')).toBeInTheDocument()
    expect(screen.getByText(/Este plan se generó a partir del contexto del repositorio/)).toBeInTheDocument()
    expect(initMutation.mutateAsync).toHaveBeenCalledWith({ company_name: 'Fábrica de demostración', default_scope_size: 'M' })
    expect(autoMutation.mutateAsync).toHaveBeenCalledWith({ company_id: 77, scope_size: 'M' })
  })

  it.each([
    [usePlanSprint, 'Planificando…'],
    [useStartSprint, 'Iniciando…'],
    [usePlanSelfImprovementSprint, 'Analizando…'],
  ] as const)('localizes pending mutation labels', (hook, label) => {
    vi.mocked(hook).mockReturnValue(mutation({ isPending: true }) as never)
    renderDashboard()
    expect(screen.getByRole('button', { name: label })).toBeDisabled()
  })

  it('localizes the loading state and generic failure while preserving external error messages', () => {
    vi.mocked(useSprintBoard).mockReturnValue({ data: undefined, isLoading: true } as never)
    vi.mocked(useStartSprint).mockReturnValue(mutation({ error: 'failure' }) as never)
    const view = renderDashboard()
    expect(screen.getByText('Cargando datos del sprint...')).toBeInTheDocument()
    expect(screen.getByText('No se pudo iniciar el sprint')).toBeInTheDocument()

    vi.mocked(useStartSprint).mockReturnValue(mutation({ error: new Error('External service error') }) as never)
    view.rerender(<MemoryRouter><Dashboard sprintId={null} /></MemoryRouter>)
    expect(screen.getByText('External service error')).toBeInTheDocument()
  })

  it('uses English defaultValue text rather than dotted keys when resources are missing', async () => {
    const missingResources = createInstance()
    await missingResources.init({ lng: 'en', fallbackLng: false, resources: {} })
    render(
      <I18nextProvider i18n={missingResources}>
        <MemoryRouter><Dashboard sprintId={null} /></MemoryRouter>
      </I18nextProvider>,
    )
    expect(screen.getByText('AI Sprint command center')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate Plan' })).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Describe the sprint goal…')).toBeInTheDocument()
    expect(screen.queryByText(/dashboard\./)).not.toBeInTheDocument()
  })
})
