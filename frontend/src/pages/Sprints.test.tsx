import { render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Sprints from './Sprints'
import { api, type SprintSummary } from '../services/api'
import { specificationsApi } from '../services/specifications'
import { managerApi, type ManagerRecord } from '../services/manager'
import { routingApi } from '../services/routing'
import { gateRefutationsApi } from '../services/gateRefutations'
import { missingModuleMentionsApi } from '../services/missingModuleMentions'
import { queryClient } from '../lib/queryClient'
import type { CostSummary } from '../services/cost'
import type { ManagedSprint } from '../services/sprintAdmin'
import { useAppStore } from '../store/useAppStore'

const sprints: SprintSummary[] = [
  { id: 12, sprint_name: 'Docs polish', status: 'completed', goal: null, start_date: '2026-10-10', scope_size: 'S', total: 4, done: 4, blocked: 0, rework: 0, second_attempts: { total: 0, done: 0, briefed: 0, briefed_done: 0 }, console_advice: { advised: { total: 0, done: 0, rework: 0 }, unadvised: { total: 0, done: 0, rework: 0 } }, architect_advice: { advised: { total: 0, done: 0, rework: 0 }, unadvised: { total: 0, done: 0, rework: 0 } } },
  { id: 9, sprint_name: 'Hardening', status: 'active', goal: 'Close audit gaps', start_date: '2026-10-01', scope_size: 'M', total: 8, done: 3, blocked: 1, rework: 2, second_attempts: { total: 0, done: 0, briefed: 0, briefed_done: 0 }, console_advice: { advised: { total: 0, done: 0, rework: 0 }, unadvised: { total: 0, done: 0, rework: 0 } }, architect_advice: { advised: { total: 0, done: 0, rework: 0 }, unadvised: { total: 0, done: 0, rework: 0 } } },
  { id: 4, sprint_name: 'Foundation', status: 'needs_split', goal: null, start_date: null, scope_size: 'L', total: 0, done: 0, blocked: 0, rework: 0, second_attempts: { total: 0, done: 0, briefed: 0, briefed_done: 0 }, console_advice: { advised: { total: 0, done: 0, rework: 0 }, unadvised: { total: 0, done: 0, rework: 0 } }, architect_advice: { advised: { total: 0, done: 0, rework: 0 }, unadvised: { total: 0, done: 0, rework: 0 } } },
]

const counts = (overrides: Partial<ManagerRecord['total']> = {}): ManagerRecord['total'] => ({
  suggested: 0, decided: 0, agreed: 0, unusable: 0, taken_alone: 0, overridden: 0, override_rate: null, ...overrides,
})
const EMPTY_RECORD: ManagerRecord = { categories: {}, total: counts(), flagged_rules: [] }

function renderPage() {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/sprints']}><Sprints /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Sprints list', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 42, companyName: 'Example', activeSprintId: 9 })
    // The planner lists the company's approved specifications; none here.
    vi.spyOn(specificationsApi, 'list').mockResolvedValue({ specifications: [] })
    vi.spyOn(api, 'getBenchScorecards').mockResolvedValue({ available: true, cards: [] })
    vi.spyOn(managerApi, 'record').mockResolvedValue(EMPTY_RECORD)
    vi.spyOn(routingApi, 'record').mockResolvedValue({ rows: [], tasks: 0, policy: null })
    vi.spyOn(gateRefutationsApi, 'get').mockResolvedValue({
      company_id: 42, sprint_id: null, since: '2026-11-02', until: '2026-11-15', by_problem: {}, refuted: 0, unconfirmed: 0, weeks: [],
    })
    const noRequests = { since: '2026-09-17T00:00:00', until: '2026-11-12T12:33:51', days: 56.52, rework_requests: {}, requests_whose_text_names_missing_module: {} }
    vi.spyOn(missingModuleMentionsApi, 'get').mockResolvedValue({
      company_id: 42, sprint_id: null, counts_what: '', cutoff: '2026-11-12T12:33:51', cutoff_is_default: true,
      before: noRequests, after: { ...noRequests, since: '2026-11-12T12:33:51', until: '2026-11-16T00:00:00', days: 3.48 }, weeks: [],
    })
  })
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('renders each sprint with status, progress and start date, active first, linking to its page', async () => {
    const list = vi.spyOn(api, 'listSprints').mockResolvedValue(sprints)
    renderPage()
    const table = await screen.findByRole('list', { name: 'Sprints' })
    const rows = within(table).getAllByRole('listitem')
    expect(rows.map(row => within(row).getByRole('link').textContent)).toEqual(['Hardening', 'Docs polish', 'Foundation'])
    expect(within(rows[0]).getByRole('link')).toHaveAttribute('href', '/sprints/9')
    expect(within(rows[1]).getByRole('link')).toHaveAttribute('href', '/sprints/12')
    expect(within(rows[2]).getByRole('link')).toHaveAttribute('href', '/sprints/4')
    expect(rows[0]).toHaveTextContent('Active')
    expect(rows[0]).toHaveTextContent('3 / 8 done')
    expect(rows[0]).toHaveTextContent('38%')
    expect(rows[0]).toHaveTextContent('1 blocked · 2 rework requests')
    expect(rows[1]).not.toHaveTextContent('blocked')
    expect(rows[0]).toHaveTextContent('2026-10-01')
    expect(rows[0]).toHaveTextContent('Last opened')
    expect(rows[1]).toHaveTextContent('Completed')
    expect(rows[1]).toHaveTextContent('4 / 4 done')
    expect(rows[2]).toHaveTextContent('Needs Split')
    expect(rows[2]).toHaveTextContent('Not started')
    expect(list).toHaveBeenCalledWith(42)
  })

  it("draws the bench's reference under the learning tiles from the latest valid scorecard", async () => {
    vi.spyOn(api, 'listSprints').mockResolvedValue(sprints)
    const unmeasured = { measured: false as const, reason: 'not_in_card' as const }
    vi.spyOn(api, 'getBenchScorecards').mockResolvedValue({ available: true, cards: [{
      file: '20261114T201804Z.json', folder: '', stamp: '20261114T201804Z', problems: [], status: 'valid', schema_version: 3,
      commit: 'c'.repeat(40), llm: 'fake', policy: null, run: null, graders_sha: null, sections_off: [], has_ablation: false,
      totals: { specs: 1, specs_completed: 1, tasks: 4, done_first_attempt: 3, first_attempt_rate: 0.75, rework_per_task: 0.25,
        blocked_share: 0.5, wall_clock_seconds: 70, criteria: unmeasured, prompt: unmeasured,
        tokens: { ...unmeasured, turns_reported: 0, turns_unreported: 0 } },
      specs: [], prompt_sections: null,
    }] })
    renderPage()
    expect(await screen.findByText('Bench reference: 0.25')).toBeInTheDocument()
    expect(screen.getByText('Bench reference: 50%')).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'See the bench' })[0]).toHaveAttribute('href', '/bench')
  })

  it("shows each sprint's tokens and cost, and price unknown when it could not be priced", async () => {
    const cost: CostSummary = {
      turns: 3, reported_turns: 3, estimated_turns: 0, unavailable_turns: 0, reported_tokens: 4000,
      input_tokens: 3000, output_tokens: 1000, estimated_tokens: 0, cost_cents: 42, reason: null,
      unpriced_models: [], models: ['coder'], price_source: 'models.dev',
    }
    const costed: ManagedSprint[] = [
      { ...sprints[0], cost },
      { ...sprints[1], cost: { ...cost, cost_cents: null, reason: 'price_unknown', unpriced_models: ['mystery'] } },
    ]
    vi.spyOn(api, 'listSprints').mockResolvedValue(costed)
    renderPage()
    const table = await screen.findByRole('list', { name: 'Sprints' })
    const [hardening, docs] = within(table).getAllByRole('listitem')
    expect(docs).toHaveTextContent('$0.42')
    expect(docs).toHaveTextContent('3.0k in · 1.0k out tokens')
    expect(hardening).toHaveTextContent('price unknown')
    expect(hardening).not.toHaveTextContent('$')
  })

  it('offers a new company and sprint planning when the company has no sprints', async () => {
    vi.spyOn(api, 'listSprints').mockResolvedValue([])
    renderPage()
    expect(await screen.findByText('No sprints yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'New company' })).toHaveAttribute('href', '/companies/new')
    expect(screen.getByRole('link', { name: 'Plan a sprint' })).toHaveAttribute('href', '#plan-sprint')
    const planner = screen.getByRole('region', { name: 'Plan a sprint' })
    expect(planner).toHaveAttribute('id', 'plan-sprint')
    expect(within(planner).getByRole('button', { name: 'Generate Plan' })).toBeInTheDocument()
  })

  it('asks for a company instead of listing when none is selected', () => {
    const list = vi.spyOn(api, 'listSprints')
    useAppStore.setState({ companyId: null })
    renderPage()
    expect(screen.getByText('Select a company to see its sprints.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Select company' })).toHaveAttribute('href', '/companies/select')
    expect(list).not.toHaveBeenCalled()
  })

  it("shows the Manager agent's record beside the other numbers once it has decisions", async () => {
    vi.spyOn(api, 'listSprints').mockResolvedValue(sprints)
    const record = vi.spyOn(managerApi, 'record').mockResolvedValue({
      categories: {
        accept_delivery: counts({ suggested: 3, decided: 2, agreed: 1, taken_alone: 4, overridden: 1, override_rate: 0.25 }),
      },
      total: counts({ suggested: 3, decided: 2, agreed: 1, taken_alone: 4, overridden: 1, override_rate: 0.25 }),
      flagged_rules: ['accept_delivery'],
    })
    renderPage()
    const card = await screen.findByRole('region', { name: "The Manager agent's record" })
    expect(record).toHaveBeenCalledWith(42)
    expect(within(card).getByText('accept_delivery')).toBeInTheDocument()
    expect(card).toHaveTextContent('matched your decision 1 of 2 · overridden 1 of 4 taken alone (25%)')
    expect(card).toHaveTextContent('Flagged, not applied: accept_delivery')
  })

  it('shows no record while the Manager agent has decided nothing', async () => {
    vi.spyOn(api, 'listSprints').mockResolvedValue(sprints)
    renderPage()
    await screen.findByRole('list', { name: 'Sprints' })
    expect(screen.queryByRole('region', { name: "The Manager agent's record" })).not.toBeInTheDocument()
  })
})
