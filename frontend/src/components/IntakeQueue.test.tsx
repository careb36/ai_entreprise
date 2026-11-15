import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { IntakeQueue } from './IntakeQueue'
import { useDecideIntake, useIntake, useIntakeRecord, useSendIntake, type IntakeItem } from '../services/intake'
import { useSprints } from '../hooks/useQueries'

vi.mock('../services/intake', async () => {
  const actual = await vi.importActual<typeof import('../services/intake')>('../services/intake')
  return { ...actual, useIntake: vi.fn(), useIntakeRecord: vi.fn(), useDecideIntake: vi.fn(), useSendIntake: vi.fn() }
})
vi.mock('../services/specifications', () => ({
  useSpecifications: () => ({ data: { specifications: [{ id: 4, title: 'Task board', version: 2, status: 'approved' }] } }),
}))
vi.mock('../hooks/useQueries', () => ({ useSprints: vi.fn() }))

const decide = vi.fn()
const send = vi.fn()

const pending: IntakeItem = {
  id: 9,
  company_id: 7,
  source: 'ticket',
  source_ref: 'FAB-00012',
  submitted_by: 'ana@example.com',
  title: 'Export the board as CSV',
  body: 'So we can share it.',
  status: 'pending',
  snoozed_until: null,
  duplicate_of: null,
  outcome: null,
  suggestion: { status: 'duplicate', category: 'feature', duplicate_of: 'intake:3', reason: 'Asked before.', by: 'Support agent', at: '2026-11-15T10:00:00' },
  decision: null,
  created_at: '2026-11-15T09:00:00',
  updated_at: '2026-11-15T09:00:00',
}

function mockList(items: IntakeItem[], pendingCount = items.length) {
  vi.mocked(useIntake).mockReturnValue({ data: { items, pending: pendingCount, statuses: [], categories: [] }, isLoading: false, isError: false } as never)
}

function renderQueue(canManage = true) {
  return render(<MemoryRouter><IntakeQueue companyId={7} canManage={canManage} /></MemoryRouter>)
}

describe('IntakeQueue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockList([pending])
    vi.mocked(useIntakeRecord).mockReturnValue({ data: undefined } as never)
    vi.mocked(useDecideIntake).mockReturnValue({ mutate: decide, isPending: false, isError: false, error: null } as never)
    vi.mocked(useSendIntake).mockReturnValue({ mutate: send, isPending: false, isError: false, error: null } as never)
    vi.mocked(useSprints).mockReturnValue({
      data: [
        { id: 2, sprint_name: 'Sprint 2', status: 'active' },
        { id: 1, sprint_name: 'Sprint 1', status: 'completed' },
      ],
    } as never)
  })

  it('lists pending requests with their source and the agent suggestion as a claim', () => {
    renderQueue()
    const item = screen.getByRole('listitem', { name: 'Export the board as CSV' })
    expect(item).toHaveTextContent('Ticket · ana@example.com · Pending')
    expect(item).toHaveTextContent('Support agent suggests (a claim): Duplicate, feature. Asked before. (intake:3)')
    // The suggestion is not filled into the decision.
    expect(within(item).getByLabelText('Decision')).toHaveValue('accept_task')
    expect(within(item).getByLabelText('Category')).toHaveValue('')
  })

  it('accepts as a task in an open sprint only', () => {
    renderQueue()
    const submit = screen.getByRole('button', { name: 'Record the decision' })
    expect(submit).toBeDisabled()
    const sprint = screen.getByLabelText('Sprint')
    expect(within(sprint).queryByRole('option', { name: 'Sprint 1' })).toBeNull()
    fireEvent.change(sprint, { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('Category'), { target: { value: 'feature' } })
    fireEvent.click(submit)
    expect(decide).toHaveBeenCalledWith({
      id: 9,
      decision: { action: 'accept', target: 'task', sprint_id: 2, assignee: 'PO', category: 'feature', reason: null },
    })
  })

  it('accepts into a specification revision', () => {
    renderQueue()
    fireEvent.change(screen.getByLabelText('Decision'), { target: { value: 'accept_specification' } })
    fireEvent.change(screen.getByLabelText('Specification (an approved one gets a new revision)'), { target: { value: '4' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record the decision' }))
    expect(decide).toHaveBeenCalledWith({
      id: 9,
      decision: { action: 'accept', target: 'specification', specification_id: 4, category: null, reason: null },
    })
  })

  it('needs a reason to decline, and names the duplicate', () => {
    renderQueue()
    fireEvent.change(screen.getByLabelText('Decision'), { target: { value: 'decline' } })
    const submit = screen.getByRole('button', { name: 'Record the decision' })
    expect(submit).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Why it is declined (required)'), { target: { value: 'Out of scope' } })
    fireEvent.click(submit)
    expect(decide).toHaveBeenLastCalledWith({ id: 9, decision: { action: 'decline', category: null, reason: 'Out of scope' } })

    fireEvent.change(screen.getByLabelText('Decision'), { target: { value: 'duplicate' } })
    fireEvent.change(screen.getByLabelText('Duplicate of (intake:ID or task:ID)'), { target: { value: 'task:5' } })
    fireEvent.click(submit)
    expect(decide).toHaveBeenLastCalledWith({ id: 9, decision: { action: 'duplicate', of_task_id: 5, category: null, reason: 'Out of scope' } })
  })

  it('snoozes until a date', () => {
    renderQueue()
    fireEvent.change(screen.getByLabelText('Decision'), { target: { value: 'snooze' } })
    fireEvent.change(screen.getByLabelText('Snooze until'), { target: { value: '2026-12-01' } })
    fireEvent.click(screen.getByRole('button', { name: 'Record the decision' }))
    const call = decide.mock.calls[0][0]
    expect(call.decision.action).toBe('snooze')
    expect(call.decision.until).toBe(new Date('2026-12-01').toISOString())
  })

  it('shows a Viewer no decision, but lets them send a request', () => {
    renderQueue(false)
    expect(screen.queryByRole('button', { name: 'Record the decision' })).toBeNull()
    fireEvent.change(screen.getByLabelText('Request title'), { target: { value: '  Dark mode ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send to the queue' }))
    expect(send).toHaveBeenCalledWith({ title: 'Dark mode', body: '' }, expect.anything())
  })

  it('links a decided request to what it became, and reopens a declined one', () => {
    mockList([
      { ...pending, id: 10, title: 'Accepted one', status: 'accepted', outcome: { kind: 'task', id: 33 }, decision: { status: 'accepted', category: 'feature', reason: null, by: 'mia@example.com', at: '2026-11-15T11:00:00' } },
      { ...pending, id: 11, title: 'Declined one', status: 'declined', suggestion: null, decision: { status: 'declined', category: null, reason: 'Out of scope', by: 'mia@example.com', at: '2026-11-15T11:00:00' } },
    ], 0)
    renderQueue()
    const accepted = screen.getByRole('listitem', { name: 'Accepted one' })
    expect(within(accepted).getByRole('link', { name: 'task #33' })).toHaveAttribute('href', '/task/33')
    expect(accepted).toHaveTextContent('Decided Accepted by mia@example.com')
    expect(within(accepted).queryByRole('button', { name: 'Back to pending' })).toBeNull()
    const declined = screen.getByRole('listitem', { name: 'Declined one' })
    fireEvent.click(within(declined).getByRole('button', { name: 'Back to pending' }))
    expect(decide).toHaveBeenCalledWith({ id: 11, decision: { action: 'reopen' } })
  })

  it('filters by status and shows the numbers', () => {
    vi.mocked(useIntakeRecord).mockReturnValue({
      data: {
        received: 4,
        by_status: {},
        recorded: 2,
        recorded_share: 0.5,
        median_seconds_to_decision: 7200,
        agreement: { feature: { decided: 2, agreed: 1, unusable: 0 } },
        category_agreement: { compared: 2, agreed: 1 },
      },
    } as never)
    renderQueue()
    fireEvent.click(screen.getByRole('button', { name: 'Snoozed' }))
    expect(vi.mocked(useIntake)).toHaveBeenLastCalledWith(7, 'snoozed')
    expect(screen.getByText('50% closed with what it led to recorded (2 of 4)')).toBeInTheDocument()
    expect(screen.getByText('Median time to a decision: 2 h')).toBeInTheDocument()
    expect(screen.getByText('Feature: the Support agent matched the person on 1 of 2')).toBeInTheDocument()
  })
})
