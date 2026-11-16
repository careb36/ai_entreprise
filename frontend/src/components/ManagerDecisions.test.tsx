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

  it('shows why the policy did not let the agent decide alone, apart from its claim', () => {
    const refused = {
      ...DECISION, decided_by: null, rule: null, outcome: null, action_state: 'not_allowed',
      refusal_reason: 'accept_delivery: an operator froze the task',
    }
    const { rerender } = render(<ManagerSuggestion suggestion={refused} />)
    // The claim stays the model's; the refusal is the platform's own line.
    expect(screen.getByText('Manager agent suggests: accept')).toBeInTheDocument()
    expect(screen.getByText('Not decided alone: accept_delivery: an operator froze the task')).toBeInTheDocument()
    rerender(<ManagerSuggestion suggestion={{ ...refused, refusal_reason: null }} />)
    expect(screen.queryByText(/Not decided alone/)).not.toBeInTheDocument()
  })

  it('lists the refusals in the brief with their reasons', () => {
    vi.mocked(useClientBrief).mockReturnValue({
      data: {
        since: '2026-11-07T00:00:00', done: { count: 0 },
        agent: {
          suggested: 2, taken_alone: 0, overridden: 0, refused: 2,
          refusals: [
            { id: 8, item_kind: 'task', item_id: 3, category: 'accept_delivery', decision: 'accept', reason: 'Looks fine.', action_state: 'not_allowed', refusal_reason: 'no rule of the policy applies' },
            { id: 9, item_kind: 'run', item_id: 4, category: 'approve_run', decision: null, reason: null, action_state: 'stale', refusal_reason: 'the item no longer waits for a decision' },
          ],
        },
        waiting: { total: 0 }, cost: { tokens_since: 0, tokens_total: 0, over_budget: false },
      },
    } as never)
    render(<ClientBriefCard companyId={42} />)
    expect(screen.getByText('Not decided alone (2)')).toBeInTheDocument()
    expect(screen.getByText('accept (task #3): no rule of the policy applies')).toBeInTheDocument()
    expect(screen.getByText('no usable suggestion (run #4): the item no longer waits for a decision')).toBeInTheDocument()
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
