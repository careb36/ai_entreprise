import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ManagerPolicyPanel } from './ManagerPolicyPanel'
import { MANAGER_RULES, useManagerPolicy, useSaveManagerPolicy, type ManagerPolicy } from '../services/manager'

vi.mock('../services/manager', async () => {
  const actual = await vi.importActual<typeof import('../services/manager')>('../services/manager')
  return { ...actual, useManagerPolicy: vi.fn(), useSaveManagerPolicy: vi.fn() }
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
})
