import { fireEvent, render, screen, within } from '@testing-library/react'
import i18n from 'i18next'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EvidenceDetail from './EvidenceDetail'
import {
  useAgentRun,
  useAgentRunApprovals,
  useAgentRunVerifications,
  useApproveAgentRun,
  useAuditVerification,
  useVerifyAgentRun,
} from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'

vi.mock('../hooks/useQueries', () => ({
  useAgentRun: vi.fn(),
  useAgentRunApprovals: vi.fn(),
  useAgentRunVerifications: vi.fn(),
  useApproveAgentRun: vi.fn(),
  useAuditVerification: vi.fn(),
  useVerifyAgentRun: vi.fn(),
}))

const mockedUseAgentRun = vi.mocked(useAgentRun)
const mockedUseAgentRunApprovals = vi.mocked(useAgentRunApprovals)
const mockedUseAgentRunVerifications = vi.mocked(useAgentRunVerifications)
const mockedUseApproveAgentRun = vi.mocked(useApproveAgentRun)
const mockedUseAuditVerification = vi.mocked(useAuditVerification)
const mockedUseVerifyAgentRun = vi.mocked(useVerifyAgentRun)
const approveMutation = vi.fn()

function setup() {
  return render(
    <MemoryRouter initialEntries={['/evidence/7']}>
      <Routes><Route path="/evidence/:runId" element={<EvidenceDetail />} /></Routes>
    </MemoryRouter>,
  )
}

describe('Evidence detail', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    void i18n.changeLanguage('en')
    useAppStore.setState({ companyId: 42, companyName: 'Example', activeSprintId: null })
    mockedUseAgentRun.mockReturnValue({
      data: {
        id: 7,
        company_id: 42,
        external_ref: 'issue-27-run',
        version: 1,
        producer: 'github',
        model: null,
        agent_role: 'Developer',
        subject: null,
        claims: { title: 'Adds backend/app/crypto.py with Fernet encryption', summary: 'Crypto implementation added' },
        artifact_text: '',
        artifact_digest: 'digest',
        artifact_size: 0,
        status: 'refuted',
        started_at: null,
        finished_at: null,
        ingested_at: '2026-10-15T12:00:00Z',
        audit_log_id: 10,
        latest_approval: null,
      },
      isLoading: false,
      isError: false,
    } as never)
    mockedUseAgentRunVerifications.mockReturnValue({
      data: {
        verifications: [{
          id: 2,
          verdict: 'refuted',
          checks: [{
            name: 'artifact_nonempty',
            outcome: 'refuted',
            observation: 'claims present but artifact is empty (0 bytes)',
          }],
          evidence: null,
          observed_at: '2026-10-15T12:30:00Z',
          audit_log_id: 11,
        }],
      },
      isLoading: false,
      isError: false,
    } as never)
    mockedUseAgentRunApprovals.mockReturnValue({
      data: { approvals: [] },
      isLoading: false,
      isError: false,
    } as never)
    mockedUseAuditVerification.mockReturnValue({
      data: {
        interaction_id: 'agent-run:issue-27-run',
        summary: { valid: 0, invalid: 0, unsigned: 1, legacy: 1 },
        rows: [
          { id: 10, action: 'agent_run_ingested', status: 'unsigned', mode: 'unsigned' },
          { id: 11, action: 'agent_run_verified', status: 'legacy', mode: 'legacy' },
        ],
      },
      isLoading: false,
      isError: false,
      error: null,
    } as never)
    mockedUseVerifyAgentRun.mockReturnValue({ mutate: vi.fn(), isPending: false, isError: false } as never)
    mockedUseApproveAgentRun.mockReturnValue({
      mutate: approveMutation,
      isPending: false,
      isError: false,
      isSuccess: false,
    } as never)
  })

  it('shows a claim beside the concrete observation', () => {
    setup()
    expect(screen.getByText('Adds backend/app/crypto.py with Fernet encryption')).toBeInTheDocument()
    const observedPanel = screen.getByText(i18n.t('evidence.observed')).closest('.card') as HTMLElement
    expect(within(observedPanel).getByText('claims present but artifact is empty (0 bytes)')).toBeInTheDocument()
    expect(screen.getByText('artifact_nonempty')).toBeInTheDocument()
  })

  it('renders unsigned and legacy audit rows without the valid style', () => {
    setup()
    const unsigned = screen.getByTestId('signature-row-unsigned')
    const legacy = screen.getByTestId('signature-row-legacy')
    expect(unsigned).toHaveTextContent(i18n.t('evidence.signature.unsigned'))
    expect(legacy).toHaveTextContent(i18n.t('evidence.signature.legacy'))
    expect(unsigned.className).not.toContain('emerald')
    expect(legacy.className).not.toContain('emerald')
    expect(screen.getByTestId('signature-unsigned')).toHaveTextContent('1')
    expect(screen.getByTestId('signature-legacy')).toHaveTextContent('1')
  })

  it('does not submit a rejection with blank feedback and shows validation', () => {
    setup()
    fireEvent.change(screen.getByLabelText(i18n.t('evidence.approverName')), { target: { value: 'Reviewer' } })
    fireEvent.change(screen.getByLabelText(i18n.t('evidence.decision')), { target: { value: 'rejected' } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('evidence.submitReview') }))

    expect(approveMutation).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent(i18n.t('evidence.feedbackRequired'))
  })

  it('flags legacy company ownership separately from signature verification', () => {
    mockedUseAuditVerification.mockReturnValue({
      data: {
        interaction_id: 'agent-run:issue-27-run',
        summary: { valid: 1, invalid: 0, unsigned: 0, legacy: 0 },
        rows: [{ id: 10, action: 'old', status: 'valid', mode: 'hmac-sha256', company_id: null, legacy: true }],
      },
      isLoading: false,
      isError: false,
      error: null,
    } as never)
    setup()
    expect(screen.getByTestId('legacy-company')).toHaveTextContent(i18n.t('evidence.legacyCompany'))
    expect(screen.getByTestId('legacy-company').className).not.toContain('emerald')
    expect(screen.getByTestId('signature-row-valid')).toBeInTheDocument()
  })

  it('submits the exact rejection body when feedback is provided', () => {
    setup()
    fireEvent.change(screen.getByLabelText(i18n.t('evidence.approverName')), { target: { value: ' Reviewer ' } })
    fireEvent.change(screen.getByLabelText(i18n.t('evidence.decision')), { target: { value: 'rejected' } })
    fireEvent.change(screen.getByLabelText(`${i18n.t('evidence.feedback')} *`), { target: { value: '  Missing artifact  ' } })
    fireEvent.change(screen.getByLabelText(i18n.t('evidence.rejectionCategory')), { target: { value: 'security' } })
    fireEvent.click(screen.getByRole('button', { name: i18n.t('evidence.submitReview') }))

    expect(approveMutation).toHaveBeenCalledWith({
      company_id: 42,
      approver_name: 'Reviewer',
      decision: 'rejected',
      feedback: 'Missing artifact',
      rejection_category: 'security',
    })
  })
})
