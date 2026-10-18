import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { GovernancePanel } from './GovernancePanel'
import { useAgentRuns, useQuotas, useResetQuota } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'

vi.mock('../hooks/useQueries', () => ({
  useAgentRuns: vi.fn(),
  useQuotas: vi.fn(),
  useResetQuota: vi.fn(),
}))

const mockedUseAgentRuns = vi.mocked(useAgentRuns)
const mockedUseQuotas = vi.mocked(useQuotas)
const mockedUseResetQuota = vi.mocked(useResetQuota)

describe('GovernancePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ companyId: 42, companyName: 'Example', activeSprintId: null })
    mockedUseAgentRuns.mockReturnValue({
      data: {
        agent_runs: [
          { id: 1, external_ref: 'needs-review', producer: 'producer-a', agent_role: 'Developer', latest_approval: null },
          { id: 2, external_ref: 'already-reviewed', producer: 'producer-b', agent_role: 'QA', latest_approval: { decision: 'approved' } },
        ],
      },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as never)
    mockedUseQuotas.mockReturnValue({ data: undefined } as never)
    mockedUseResetQuota.mockReturnValue({ mutate: vi.fn(), isPending: false } as never)
  })

  it('lists only runs without a latest approval and links to their evidence', () => {
    render(<MemoryRouter><GovernancePanel /></MemoryRouter>)

    expect(screen.getByText('needs-review')).toBeInTheDocument()
    expect(screen.queryByText('already-reviewed')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Review evidence/ })).toHaveAttribute('href', '/evidence/1')
    expect(screen.getByRole('heading', { name: 'Review' })).toBeInTheDocument()
    expect(mockedUseQuotas).not.toHaveBeenCalled()
    expect(mockedUseResetQuota).not.toHaveBeenCalled()
  })
})
