import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ClientBriefCard, ManagerDecisions, ManagerSuggestion } from './ManagerDecisions'
import { useClientBrief, useOverrideManagerDecision, type ManagerDecision } from '../services/manager'

vi.mock('../services/manager', () => ({
  useClientBrief: vi.fn(),
  useOverrideManagerDecision: vi.fn(),
}))

const mutate = vi.fn()

const DECISION: ManagerDecision = {
  id: 5, item_kind: 'task', item_id: 12, category: 'accept_delivery',
  suggested_decision: 'accept', suggested_reason: 'Verified, low risk, two files.',
  suggested_by: 'Manager agent', suggested_at: null,
  decided_by: 'Manager agent', rule: 'accept_delivery', outcome: 'accepted', decided_at: null,
  overridden_by: null, overridden_at: null,
}

describe('ManagerDecisions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useOverrideManagerDecision).mockReturnValue({ mutate, isPending: false, isError: false, error: null } as never)
  })

  it('shows a decision taken alone with its rule, and a Manager overrides it in one click', () => {
    render(<ManagerDecisions decisions={[DECISION]} canManage />)
    expect(screen.getByText('Decided by the Manager agent: accept (task #12)')).toBeInTheDocument()
    expect(screen.getByText('Allowed by the rule accept_delivery')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Override/ }))
    expect(mutate).toHaveBeenCalledWith(5)
  })

  it('offers no override to a Viewer, nor on one already overridden', () => {
    const { rerender } = render(<ManagerDecisions decisions={[DECISION]} canManage={false} />)
    expect(screen.queryByRole('button', { name: /Override/ })).not.toBeInTheDocument()
    rerender(<ManagerDecisions decisions={[{ ...DECISION, overridden_by: 'ana' }]} canManage />)
    expect(screen.queryByRole('button', { name: /Override/ })).not.toBeInTheDocument()
    expect(screen.getByText('Overridden by ana')).toBeInTheDocument()
  })

  it('renders nothing without decisions', () => {
    const { container } = render(<ManagerDecisions decisions={[]} canManage />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows a suggestion as a claim, and says when there is none to use', () => {
    const suggestion = { ...DECISION, decided_by: null, rule: null, outcome: null }
    const { rerender } = render(<ManagerSuggestion suggestion={suggestion} />)
    expect(screen.getByText('Manager agent suggests: accept')).toBeInTheDocument()
    rerender(<ManagerSuggestion suggestion={{ ...suggestion, suggested_decision: null }} />)
    expect(screen.getByText(/could not suggest/)).toBeInTheDocument()
  })

  it("reads the client's brief", () => {
    vi.mocked(useClientBrief).mockReturnValue({
      data: {
        since: '2026-11-07T00:00:00', done: { count: 3 },
        agent: { suggested: 4, taken_alone: 2, overridden: 1 },
        waiting: { total: 5 }, cost: { tokens_since: 900, tokens_total: 9000, over_budget: true },
      },
    } as never)
    render(<ClientBriefCard companyId={42} />)
    expect(screen.getByText(/3 tasks done, 2 decided by the Manager agent \(1 overridden\), 5 waiting for you, 900 tokens/)).toBeInTheDocument()
    expect(screen.getByText(/Over the token budget/)).toBeInTheDocument()
  })
})
