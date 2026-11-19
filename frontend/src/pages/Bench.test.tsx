import { render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Bench from './Bench'
import { queryClient } from '../lib/queryClient'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const SHA = '0123456789abcdef0123456789abcdef01234567'
const prompt = {
  prompts: 4, prompt_sections_missing: 0, tokens_estimated: 6854, tokens_mean: 1713.5,
  system_tokens_estimated: 102, cut_prompts: 0, related_code_prompts: 0,
}

function spec(name: string, overrides: Record<string, unknown> = {}) {
  return {
    name, status: 'completed', error: null,
    tasks: { total: 4, done: 4, blocked: 0, done_first_attempt: 3 },
    rework_requests: 1, rework_per_task: 0.25, blocked_share: 0, first_attempt_rate: 0.75,
    wall_clock_seconds: 70, models: { Developer: ['fake-model'] },
    criteria: { measured: true, value: { passed: 4, total: 4, rate: 1 } },
    results: { 'REQ-1.1': 'pass' }, isolation: null,
    tokens: { measured: true, value: 13538, turns_reported: 8, turns_unreported: 0 },
    cost_cents: { measured: true, value: 12.5 },
    prompt: { measured: true, value: prompt },
    ...overrides,
  }
}

const LATEST = {
  file: '20261114T201804Z.json', folder: '', stamp: '20261114T201804Z', problems: [], status: 'valid',
  schema_version: 3, commit: SHA, llm: 'fake', policy: 'default', run: null, graders_sha: null,
  sections_off: [], has_ablation: false,
  totals: {
    specs: 2, specs_completed: 2, tasks: 8, done_first_attempt: 6, first_attempt_rate: 0.75,
    rework_per_task: 0.25, blocked_share: 0, wall_clock_seconds: 140,
    criteria: { measured: false, reason: 'grade_unmeasured', detail: ['02-ungraded'] },
    tokens: { measured: true, value: 13538, turns_reported: 8, turns_unreported: 3 },
    prompt: { measured: true, value: prompt },
  },
  specs: [
    spec('01-measured'),
    spec('02-ungraded', {
      criteria: { measured: false, reason: 'grade_unmeasured', detail: 'the sandbox could not run' },
      tokens: { measured: false, reason: 'no_reported_turns', turns_reported: 0, turns_unreported: 3 },
      cost_cents: { measured: false, reason: 'cost_unknown', detail: 1 },
      prompt: { measured: false, reason: 'no_prompt_measure', detail: 3 },
    }),
  ],
  prompt_sections: [
    { section: 'standards_and_procedures', tokens: 3078, prompts: 4, share: 0.4487, cut: 0 },
    { section: 'related_code', tokens: 0, prompts: 0, share: 0, cut: 1 },
  ],
}

const OLDER = {
  ...LATEST,
  file: '20261108T140751Z.json', stamp: '20261108T140751Z', schema_version: 1, policy: null,
  totals: { ...LATEST.totals, criteria: { measured: true, value: { passed: 30, total: 30, rate: 1 } } },
  specs: [spec('01-measured', {
    cost_cents: { measured: false, reason: 'not_in_card' },
    prompt: { measured: false, reason: 'not_in_card' },
  })],
  prompt_sections: null,
}

const INVALID = {
  file: '20261110T000000Z.json', folder: 'real', stamp: '20261110T000000Z', status: 'invalid',
  problems: ['totals.tokens_reported is missing'],
}

function stubApi(body: unknown, status = 200) {
  const fetch = vi.fn(async (_url: string) => response(status, body))
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderPage() {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter><Bench /></MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Bench page', () => {
  beforeEach(() => {
    queryClient.clear()
    useAuthStore.setState({ expiresAt: Date.now() + 60_000 })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows measured numbers and says "Not measured" where the card did not measure, never 0', async () => {
    const fetch = stubApi({ available: true, cards: [LATEST, INVALID, OLDER] })
    renderPage()

    const latest = await screen.findByRole('region', { name: /Latest scorecard/ })
    expect(String(fetch.mock.calls[0][0])).toContain('/v1/bench/scorecards')
    const specs = within(latest).getByRole('table', { name: 'Each specification of the latest scorecard' })
    const measured = within(specs).getByRole('row', { name: /01-measured/ })
    expect(within(measured).getByText('4/4 (100%)')).toBeInTheDocument()
    expect(within(measured).getByText('13,538')).toBeInTheDocument()
    expect(within(measured).getByText('$0.125')).toBeInTheDocument()
    expect(within(measured).queryByText('Not measured')).not.toBeInTheDocument()

    const ungraded = within(specs).getByRole('row', { name: /02-ungraded/ })
    expect(within(ungraded).getAllByText('Not measured')).toHaveLength(4)
    expect(within(ungraded).getByText(/the acceptance tests could not run/)).toBeInTheDocument()
    expect(within(ungraded).getByText(/no turn reported its token usage/)).toBeInTheDocument()
    expect(within(ungraded).queryByText('0')).not.toBeInTheDocument()
    expect(within(ungraded).queryByText(/0\/0/)).not.toBeInTheDocument()

    // The totals' grade is unmeasured because one spec's is; the unreported turns are counted apart.
    const totals = within(latest).getByRole('group', { name: 'Totals' })
    expect(within(totals).getAllByText('Not measured')).toHaveLength(1)
    expect(within(totals).getByText('+3 turns unreported')).toBeInTheDocument()
  })

  it('lists every card in the history, an off-schema one with its problems and no numbers', async () => {
    stubApi({ available: true, cards: [LATEST, INVALID, OLDER] })
    renderPage()

    const history = await screen.findByRole('table', { name: 'Every committed scorecard, newest first' })
    const rows = within(history).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    expect(within(rows[1]).getByText('Off the schema')).toBeInTheDocument()
    expect(within(rows[1]).getByText('totals.tokens_reported is missing')).toBeInTheDocument()
    expect(within(rows[1]).getByText('Real models')).toBeInTheDocument()
    expect(within(rows[2]).getByText('30/30 (100%)')).toBeInTheDocument()

    const bySpec = screen.getByRole('table', { name: /Each specification across the scorecards/ })
    const older = within(bySpec).getAllByRole('row', { name: /01-measured/ })[1]
    expect(within(older).getByText('Not measured')).toBeInTheDocument()
    expect(within(older).getByText(/schema version does not record it/)).toBeInTheDocument()
  })

  it('shows the prompt weight per section when the card has it', async () => {
    stubApi({ available: true, cards: [LATEST] })
    renderPage()

    const sections = await screen.findByRole('table', { name: /estimated tokens of each prompt section/ })
    const row = within(sections).getByRole('row', { name: /standards_and_procedures/ })
    expect(within(row).getByText('3,078')).toBeInTheDocument()
    expect(within(row).getByText('44.9%')).toBeInTheDocument()
  })

  it('has no prompt weight table for a card without one', async () => {
    stubApi({ available: true, cards: [OLDER] })
    renderPage()

    await screen.findByRole('region', { name: /Latest scorecard/ })
    expect(screen.queryByRole('region', { name: 'Prompt weight by section' })).not.toBeInTheDocument()
  })

  it('shows the HumanEval section of a v5 card, an unmeasured problem and pass@1 never as 0', async () => {
    const heTokens = { measured: true, value: 900, turns_reported: 2, turns_unreported: 0 }
    const problem = (task_id: string, outcome: unknown) => ({
      task_id, entry_point: 'has_close_elements', status: 'completed', outcome, wall_clock_seconds: 42, tokens: heTokens,
    })
    const humaneval = {
      dataset: { name: 'HumanEval', source: 'example/HumanEval.jsonl.gz', sha256: 'c'.repeat(64), license: 'MIT' },
      graded: 3, passed: 1, failed: 1, unmeasured: 1,
      pass_at_1: { measured: false, reason: 'grade_unmeasured', detail: ['HumanEval/3'] },
      tokens: { measured: true, value: 2700, turns_reported: 6, turns_unreported: 0 },
      problems: [
        problem('HumanEval/0', { measured: true, value: 'pass' }),
        problem('HumanEval/1', { measured: true, value: 'fail' }),
        problem('HumanEval/3', { measured: false, reason: 'grade_unmeasured', detail: 'the sandbox could not run' }),
      ],
      skipped: ['HumanEval/4'],
    }
    stubApi({ available: true, cards: [{ ...LATEST, schema_version: 5, humaneval }] })
    renderPage()

    const panel = await screen.findByRole('region', { name: 'HumanEval' })
    const facts = within(panel).getByRole('group', { name: 'HumanEval' })
    expect(within(facts).getByText('Not measured')).toBeInTheDocument()
    expect(within(facts).queryByText('0%')).not.toBeInTheDocument()
    expect(within(facts).getByText('2,700')).toBeInTheDocument()
    expect(within(panel).getByText(/Skipped by the token budget, never graded: HumanEval\/4/)).toBeInTheDocument()
    expect(within(panel).getByText(/MIT licence/)).toBeInTheDocument()

    const table = within(panel).getByRole('table', { name: 'Each HumanEval problem of the latest scorecard' })
    expect(within(within(table).getByRole('row', { name: /HumanEval\/0/ })).getByText('Pass')).toBeInTheDocument()
    expect(within(within(table).getByRole('row', { name: /HumanEval\/1/ })).getByText('Fail')).toBeInTheDocument()
    const unmeasured = within(table).getByRole('row', { name: /HumanEval\/3/ })
    expect(within(unmeasured).getByText('Not measured')).toBeInTheDocument()
    expect(within(unmeasured).getByText('the sandbox could not run')).toBeInTheDocument()
  })

  it('shows a measured pass@1 and no HumanEval section on a card without the block', async () => {
    const humaneval = {
      dataset: { name: 'HumanEval', source: 'x', sha256: 'c'.repeat(64), license: 'MIT' },
      graded: 2, passed: 1, failed: 1, unmeasured: 0,
      pass_at_1: { measured: true, value: 0.5 },
      tokens: { measured: false, reason: 'no_reported_turns', turns_reported: 0, turns_unreported: 2 },
      problems: [], skipped: [],
    }
    stubApi({ available: true, cards: [{ ...LATEST, schema_version: 5, humaneval }] })
    const { unmount } = renderPage()
    const facts = within(await screen.findByRole('region', { name: 'HumanEval' })).getByRole('group', { name: 'HumanEval' })
    expect(within(facts).getByText('50%')).toBeInTheDocument()
    unmount()

    queryClient.clear()
    stubApi({ available: true, cards: [{ ...LATEST, humaneval: null }] })
    renderPage()
    await screen.findByRole('region', { name: /Latest scorecard/ })
    expect(screen.queryByRole('region', { name: 'HumanEval' })).not.toBeInTheDocument()
  })

  it('says when the backend finds no scorecards folder', async () => {
    stubApi({ available: false, cards: [] })
    renderPage()

    expect(await screen.findByText('No scorecards are available')).toBeInTheDocument()
  })

  it('shows an error with a retry when the request fails', async () => {
    stubApi({ detail: 'Not allowed' }, 403)
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('The scorecards could not be loaded: Not allowed')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
