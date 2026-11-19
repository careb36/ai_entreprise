import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import RunWindowPanel from './RunWindowPanel'
import { queryClient } from '../lib/queryClient'
import { api, type RunWindowState } from '../services/api'

const DEEPSEEK = 'provider_off_peak:deepseek'
const OFF: RunWindowState = {
  company_id: 4,
  run_window: null,
  now: '2026-11-25T01:30:00+00:00',
  open: true,
  next_open: null,
  next_change: null,
  preview: [],
  presets: {
    [DEEPSEEK]: {
      provider: 'deepseek',
      zone: 'UTC',
      weekdays: ['mon', 'tue', 'wed', 'thu', 'fri'],
      peak: [['01:00', '04:00'], ['06:00', '10:00']],
      holiday_calendar: 'cn',
      off_peak_price: '50% of the peak price',
      source: 'https://api-docs.deepseek.com/quick_start/pricing',
      read_on: '2026-11-19',
    },
  },
  calendars: { cn: { name: "Public holidays of the People's Republic of China", years: [] } },
}
const SET: RunWindowState = {
  ...OFF,
  run_window: { timezone: 'America/Montevideo', preset: DEEPSEEK, intervals: [], holidays: { calendar: null, dates: [] } },
  open: false,
  next_open: '2026-11-25T01:00:00-03:00',
  next_change: '2026-11-25T01:00:00-03:00',
  preview: [{ from: '2026-11-25T01:00:00-03:00', to: '2026-11-25T03:00:00-03:00' }],
}

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

describe('the run window on the company settings', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('lets an Owner choose the off-peak preset and shows when it opens and the next days', async () => {
    vi.spyOn(api, 'getRunWindow').mockResolvedValue(OFF)
    const save = vi.spyOn(api, 'setRunWindow').mockResolvedValue(SET)
    wrap(<RunWindowPanel companyId={4} isOwner />)

    expect(await screen.findByRole('radio', { name: /Off: work starts at once/ })).toBeChecked()
    fireEvent.click(screen.getByRole('radio', { name: /DeepSeek's off-peak hours/ }))
    expect(screen.getByRole('link', { name: /api-docs.deepseek.com/ })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: /Time zone/ }), { target: { value: 'America/Montevideo' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(save).toHaveBeenCalledWith(4, {
      timezone: 'America/Montevideo',
      preset: DEEPSEEK,
      intervals: [],
      holidays: { calendar: null, dates: [] },
    }))
    expect(await screen.findByText('The window is closed; it opens at 2026-11-25 01:00.')).toBeInTheDocument()
    expect(screen.getByText('2026-11-25 01:00 – 03:00')).toBeInTheDocument()
  })

  it('previews custom hours without saving them', async () => {
    vi.spyOn(api, 'getRunWindow').mockResolvedValue(OFF)
    const preview = vi.spyOn(api, 'previewRunWindow').mockResolvedValue({ ...SET, run_window: { ...SET.run_window!, preset: null } })
    const save = vi.spyOn(api, 'setRunWindow')
    wrap(<RunWindowPanel companyId={4} isOwner />)

    fireEvent.click(await screen.findByRole('radio', { name: /My own hours/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }))
    await waitFor(() => expect(preview).toHaveBeenCalled())
    expect(preview.mock.calls[0][1]?.intervals[0]).toEqual({ days: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], start: '22:00', end: '06:00' })
    expect(save).not.toHaveBeenCalled()
  })

  it('is read-only for anyone but an Owner', async () => {
    vi.spyOn(api, 'getRunWindow').mockResolvedValue(SET)
    wrap(<RunWindowPanel companyId={4} isOwner={false} />)

    const preset = await screen.findByRole('radio', { name: /DeepSeek's off-peak hours/ })
    expect(preset).toBeChecked()
    expect(preset).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument()
    expect(screen.getByText('Only an Owner changes the run window.')).toBeInTheDocument()
  })
})
