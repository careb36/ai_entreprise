import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ManagerPolicyPanel } from './ManagerPolicyPanel'
import { MANAGER_RULES, useManagerPolicy, useManagerRecord, useSaveManagerPolicy, type ManagerPolicy } from '../services/manager'

vi.mock('../services/manager', async () => {
  const actual = await vi.importActual<typeof import('../services/manager')>('../services/manager')
  return { ...actual, useManagerPolicy: vi.fn(), useManagerRecord: vi.fn(), useSaveManagerPolicy: vi.fn() }
})

const mutate = vi.fn()

const POLICY: ManagerPolicy = {
  company_id: 3,
  rules: ['accept_delivery'],
  max_files: 10,
  max_criteria: 5,
  max_files_high_risk: 3,
  publish_enabled: false,
  updated_by: null,
  updated_at: null,
  catalogue: Object.fromEntries(MANAGER_RULES.map(name => [name, `What ${name} needs.`])),
  always_escalate: ['cancel_sprint', 'change_autonomy', 'spend_above_budget'],
}

describe('ManagerPolicyPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useManagerPolicy).mockReturnValue({ data: POLICY } as never)
    vi.mocked(useManagerRecord).mockReturnValue({ data: undefined } as never)
    vi.mocked(useSaveManagerPolicy).mockReturnValue({ mutate, isPending: false, isError: false, error: null } as never)
  })

  it('lists every rule, the new ones off by default, and the decisions that stay yours', () => {
    render(<ManagerPolicyPanel companyId={3} canManage />)
    expect(screen.getByRole('checkbox', { name: /Accept a delivery held only by autonomy level 1/ })).toBeChecked()
    for (const label of [
      /Merge a done task's branch/,
      /Undo a refuted delivery/,
      /Send back a blocked task/,
      /Mark a blocked task for the next sprint/,
      /Answer a draft's question/,
      /Publish a done task's branch/,
      /Resume a sprint paused for lack of credit/,
      /Approve a specification/,
      /Approve an architecture/,
      /Accept a high-risk delivery/,
    ]) {
      expect(screen.getByRole('checkbox', { name: label })).not.toBeChecked()
    }
    expect(screen.getByText(/Strict rules/)).toBeInTheDocument()
    expect(screen.getByText(/cancel_sprint, change_autonomy, spend_above_budget/)).toBeInTheDocument()
  })

  it('a Manager turns rules, limits and publishing on and saves them', () => {
    render(<ManagerPolicyPanel companyId={3} canManage />)
    fireEvent.click(screen.getByRole('checkbox', { name: /Merge a done task's branch/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /Accept a delivery held only/ }))
    fireEvent.click(screen.getByRole('checkbox', { name: /Publishing is on/ }))
    fireEvent.change(screen.getByLabelText(/high-risk delivery$/), { target: { value: '2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save the policy' }))
    expect(mutate).toHaveBeenCalledWith({
      rules: ['merge_local'],
      max_files: 10,
      max_criteria: 5,
      max_files_high_risk: 2,
      publish_enabled: true,
    })
  })

  it('a Viewer reads it with nothing to change', () => {
    render(<ManagerPolicyPanel companyId={3} canManage={false} />)
    expect(screen.queryByRole('button', { name: 'Save the policy' })).not.toBeInTheDocument()
    for (const box of screen.getAllByRole('checkbox')) expect(box).toBeDisabled()
  })

  it('shows why a save was refused', () => {
    vi.mocked(useSaveManagerPolicy).mockReturnValue({
      mutate, isPending: false, isError: true, error: new Error('unknown rule: x'),
    } as never)
    render(<ManagerPolicyPanel companyId={3} canManage />)
    expect(screen.getByRole('alert')).toHaveTextContent('Could not save the policy')
  })

  it('says nothing changed until a box is ticked, then that the change is unsaved', () => {
    render(<ManagerPolicyPanel companyId={3} canManage />)
    const button = screen.getByRole('button', { name: 'Save the policy' })
    expect(button).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent('No changes to save')
    fireEvent.click(screen.getByRole('checkbox', { name: /Merge a done task's branch/ }))
    expect(button).toBeEnabled()
    expect(screen.getByRole('status')).toHaveTextContent('Unsaved changes')
  })

  it('confirms a save and says when and by whom the policy was last saved', () => {
    vi.mocked(useManagerPolicy).mockReturnValue({
      data: { ...POLICY, updated_by: 'owner@example.test', updated_at: '2026-11-08T22:00:00' },
    } as never)
    vi.mocked(useSaveManagerPolicy).mockReturnValue({
      mutate, isPending: false, isError: false, isSuccess: true, error: null,
    } as never)
    render(<ManagerPolicyPanel companyId={3} canManage />)
    expect(screen.getByRole('status')).toHaveTextContent(/^Saved\./)
    expect(screen.getByText(/Last saved .* by owner@example\.test/)).toBeInTheDocument()
  })

  it('shows that a save is under way', () => {
    vi.mocked(useSaveManagerPolicy).mockReturnValue({
      mutate, isPending: true, isError: false, error: null,
    } as never)
    render(<ManagerPolicyPanel companyId={3} canManage />)
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled()
  })

  it('unticking and ticking a rule again is no change, whatever the order', () => {
    vi.mocked(useManagerPolicy).mockReturnValue({
      data: { ...POLICY, rules: ['accept_delivery', 'merge_local'] },
    } as never)
    render(<ManagerPolicyPanel companyId={3} canManage />)
    const box = screen.getByRole('checkbox', { name: /Accept a delivery held only/ })
    fireEvent.click(box)
    fireEvent.click(box)
    expect(screen.getByRole('button', { name: 'Save the policy' })).toBeDisabled()
  })

  it('lets a Manager save an unchanged policy to put suspended rules back in force', () => {
    vi.mocked(useManagerRecord).mockReturnValue({ data: { flagged_rules: ['accept_delivery'] } } as never)
    render(<ManagerPolicyPanel companyId={3} canManage />)
    expect(screen.getByRole('status')).toHaveTextContent(/Suspended after overrides: accept_delivery/)
    fireEvent.click(screen.getByRole('button', { name: 'Save the policy' }))
    expect(mutate).toHaveBeenCalled()
  })

  it('reads a last-saved time with a negative offset as written', () => {
    vi.mocked(useManagerPolicy).mockReturnValue({
      data: { ...POLICY, updated_by: 'owner@example.test', updated_at: '2026-11-08T17:00:00-05:00' },
    } as never)
    render(<ManagerPolicyPanel companyId={3} canManage />)
    const expected = new Date('2026-11-08T22:00:00Z').toLocaleString()
    expect(screen.getByText(new RegExp(`Last saved ${expected.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`))).toBeInTheDocument()
  })
})
