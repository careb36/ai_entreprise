import { fireEvent, render, screen, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CalibrationReport } from './CalibrationReport'
import { calibrationApi, type CalibrationNumbers, type CalibrationReport as Report } from '../services/calibration'
import { queryClient } from '../lib/queryClient'

const numbers = (overrides: Partial<CalibrationNumbers> = {}): CalibrationNumbers => ({
  tasks: 4, done: 4, mean_predicted: 1.5, rework_cycles_per_task: 1.25, over_one_rework: 2, over_one_rework_share: 0.5,
  review_failures: 0, tokens_known: 3, tokens_unknown: 1,
  tokens_spread: { min: 100, q1: 150, median: 200, q3: 4600, max: 9000 },
  spearman: 0.31, off_by_two_bands: 1, off_by_two_bands_share: 0.3333, observed_band_unknown: 1,
  wall_seconds_median: 120, wall_unknown: 0, files_written_median: 2, files_unknown: 1, ...overrides,
})

const REPORT: Report = {
  tasks: 7,
  overall: numbers({ tasks: 7, tokens_known: 6, tokens_unknown: 1 }),
  by_predicted_band: [
    { predicted_band: 'low', ...numbers() },
    { predicted_band: 'high', ...numbers({ tasks: 2, over_one_rework: 0, over_one_rework_share: 0, tokens_unknown: 0, tokens_spread: null, spearman: null }) },
  ],
  by_role: [{ role: 'Developer', ...numbers() }, { role: null, ...numbers({ tasks: 3 }) }],
  by_producer: [{ producer: 'task-worker', ...numbers({ tasks: 7 }) }],
  by_model: [{ model: 'served-model', ...numbers({ tasks: 7 }) }],
  by_sprint_id: [{ sprint_id: 1, ...numbers() }, { sprint_id: 2, ...numbers({ tasks: 3 }) }],
  predicted_recomputed: 2,
  observed_computed: 0,
  signals: [{ signal: 'low_band_reworked', band: 'low', value: 0.5 }],
  information_only: true,
}

function renderCard() {
  return render(
    <QueryClientProvider client={queryClient}>
      <CalibrationReport companyId={42} />
    </QueryClientProvider>,
  )
}

describe('CalibrationReport', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('shows the prediction beside the observed effort, by predicted band', async () => {
    const report = vi.spyOn(calibrationApi, 'report').mockResolvedValue(REPORT)
    renderCard()
    const section = await screen.findByRole('region', { name: 'Predicted and observed effort' })
    expect(report).toHaveBeenCalledWith(42)
    expect(within(section).getByText('Rank agreement (tokens)').parentElement).toHaveTextContent('0.31')
    expect(within(section).getByText('Low tasks with >1 rework').parentElement).toHaveTextContent('50%')
    const table = within(section).getByRole('table', { name: 'Calibration by predicted band' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map(r => within(r).getByRole('rowheader').textContent)).toEqual(['Low', 'High'])
    expect(rows[0]).toHaveTextContent('50%')
    expect(rows[0]).toHaveTextContent('200 (150–4600)')
    // Unknown tokens are said apart, never shown as zero.
    expect(rows[0]).toHaveTextContent('(+1 unknown)')
    expect(rows[1]).toHaveTextContent('unknown')
    expect(rows[1]).toHaveTextContent('–')
    expect(within(section).getByText('1 task without a known token count (no turn, or a turn the provider did not report), left out of the tokens.')).toBeInTheDocument()
    expect(within(section).getByText('2 tasks from before the estimate was stored: their prediction recomputed from their plan.')).toBeInTheDocument()
  })

  it('names the signals as information, never applied', async () => {
    vi.spyOn(calibrationApi, 'report').mockResolvedValue(REPORT)
    renderCard()
    const section = await screen.findByRole('region', { name: 'Predicted and observed effort' })
    const signals = within(section).getByRole('list', { name: 'What the numbers suggest' })
    expect(signals).toHaveTextContent('50% of the low tasks needed more than one rework')
    expect(within(section).getByText('Information only: the routing policy is never changed from these numbers.')).toBeInTheDocument()
  })

  it('splits by role, producer, served model and sprint', async () => {
    vi.spyOn(calibrationApi, 'report').mockResolvedValue(REPORT)
    renderCard()
    const section = await screen.findByRole('region', { name: 'Predicted and observed effort' })
    fireEvent.click(within(section).getByRole('button', { name: 'By role' }))
    const byRole = within(section).getByRole('table', { name: 'Calibration by role' })
    expect(within(byRole).getAllByRole('rowheader').map(h => h.textContent)).toEqual(['Developer', 'none'])
    expect(within(section).getByRole('button', { name: 'By role' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(section).getByRole('button', { name: 'By sprint' }))
    const bySprint = within(section).getByRole('table', { name: 'Calibration by sprint' })
    expect(within(bySprint).getAllByRole('rowheader').map(h => h.textContent)).toEqual(['Sprint #1', 'Sprint #2'])
    fireEvent.click(within(section).getByRole('button', { name: 'By producer' }))
    expect(within(section).getByRole('table', { name: 'Calibration by producer' })).toHaveTextContent('task-worker')
  })

  it('shows nothing before a sprint has closed', async () => {
    vi.spyOn(calibrationApi, 'report').mockResolvedValue({ ...REPORT, tasks: 0 })
    const { container } = renderCard()
    await vi.waitFor(() => expect(calibrationApi.report).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})
