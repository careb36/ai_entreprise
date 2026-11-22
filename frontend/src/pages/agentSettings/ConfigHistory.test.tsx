import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConfigHistory } from './ConfigHistory'
import { queryClient } from '../../lib/queryClient'
import { api, type AgentConfigRevision } from '../../services/api'

const REVISION: AgentConfigRevision = {
  revision_id: 41,
  at: '2026-11-22T10:00:00',
  by: 'manager@example.com',
  user_id: 3,
  token_id: null,
  source: 'edit',
  reason: 'TDD trial',
  rolled_back_from: null,
  changed: ['system_prompt'],
  from: { system_prompt: null },
  to: { system_prompt: 'Write tests first.' },
}

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

describe("an agent's change history", () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('shows what changed, before and after, who and why', async () => {
    vi.spyOn(api, 'getAgentConfigRevisions').mockResolvedValue({ agent_id: 7, revisions: [REVISION] })
    wrap(<ConfigHistory agentId={7} companyId={1} readOnly />)

    expect(await screen.findByText(/Changed system_prompt/)).toBeInTheDocument()
    expect(screen.getByText(/manager@example.com/)).toBeInTheDocument()
    expect(screen.getByText('Reason: TDD trial')).toBeInTheDocument()
    expect(screen.getByText(/— → Write tests first\./)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /roll back/i })).not.toBeInTheDocument()
  })

  it('rolls a revision back for a Manager only after a second click', async () => {
    vi.spyOn(api, 'getAgentConfigRevisions').mockResolvedValue({ agent_id: 7, revisions: [REVISION] })
    const rollback = vi.spyOn(api, 'rollbackAgentConfig').mockResolvedValue({
      agent: {} as never,
      restored: ['system_prompt'],
      changed_since: ['system_prompt'],
    })
    wrap(<ConfigHistory agentId={7} companyId={1} readOnly={false} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Roll back' }))
    expect(rollback).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm roll back' }))

    await waitFor(() => expect(rollback).toHaveBeenCalledWith(7, 41))
    expect(await screen.findByText(/had changed again since that revision/)).toBeInTheDocument()
  })

  it('says so when there is no change yet', async () => {
    vi.spyOn(api, 'getAgentConfigRevisions').mockResolvedValue({ agent_id: 7, revisions: [] })
    wrap(<ConfigHistory agentId={7} companyId={1} readOnly />)
    expect(await screen.findByText('No change recorded yet.')).toBeInTheDocument()
  })
})
