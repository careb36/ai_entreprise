import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CompanyLimitsPanel from './CompanyLimitsPanel'
import { queryClient } from '../lib/queryClient'
import { api, type CompanyLimitsState } from '../services/api'

const UNSET: CompanyLimitsState = {
  company_id: 4,
  limits: null,
  defaults: { tool_rounds: 8, result_max_chars: 4000, max_output_tokens: 0, max_reworks: 3, sprint_budget_cents: 0, budget_warn_percent: 80 },
  floors: { tool_rounds: 1, result_max_chars: 500, max_output_tokens: 0, max_reworks: 0, sprint_budget_cents: 0, budget_warn_percent: 1 },
  ceilings: { tool_rounds: 50, result_max_chars: 100000, max_output_tokens: 1048576, max_reworks: 10, sprint_budget_cents: 10000000, budget_warn_percent: 100 },
}

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

describe('the company limits on the Models page', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('shows the deployment value beside each empty field and saves only what the Owner set', async () => {
    vi.spyOn(api, 'getCompanyLimits').mockResolvedValue(UNSET)
    const set = vi.spyOn(api, 'setCompanyLimits').mockResolvedValue({ ...UNSET, limits: { tool_rounds: 12 } })
    wrap(<CompanyLimitsPanel companyId={4} isOwner />)

    const rounds = await screen.findByRole('spinbutton', { name: /tool rounds/i })
    expect(rounds).toHaveAttribute('placeholder', '8')
    expect(rounds).toHaveAttribute('max', '50')
    expect(screen.getAllByText(/Deployment: 8/).length).toBeGreaterThan(0)

    fireEvent.change(rounds, { target: { value: '12' } })
    fireEvent.click(screen.getByRole('button', { name: /save limits/i }))
    await waitFor(() => expect(set).toHaveBeenCalledWith(4, { tool_rounds: 12 }))
  })

  it('goes back to the deployment values with null', async () => {
    vi.spyOn(api, 'getCompanyLimits').mockResolvedValue({ ...UNSET, limits: { max_reworks: 1 } })
    const set = vi.spyOn(api, 'setCompanyLimits').mockResolvedValue(UNSET)
    wrap(<CompanyLimitsPanel companyId={4} isOwner />)

    expect(await screen.findByDisplayValue('1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /deployment's values/i }))
    await waitFor(() => expect(set).toHaveBeenCalledWith(4, null))
  })

  it('lets a member who is not an Owner read but not change them', async () => {
    vi.spyOn(api, 'getCompanyLimits').mockResolvedValue({ ...UNSET, limits: { tool_rounds: 12 } })
    wrap(<CompanyLimitsPanel companyId={4} isOwner={false} />)

    const rounds = await screen.findByRole('spinbutton', { name: /tool rounds/i })
    expect(rounds).toHaveValue(12)
    expect(rounds).toBeDisabled()
    expect(screen.queryByRole('button', { name: /save limits/i })).toBeNull()
    expect(screen.getByText('Only an Owner can change these limits.')).toBeInTheDocument()
  })
})
