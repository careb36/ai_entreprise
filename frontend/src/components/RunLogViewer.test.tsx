import { fireEvent, render, screen } from '@testing-library/react'
import i18n from 'i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { LINES_PER_PAGE, RunLogViewer } from './RunLogViewer'
import { useRunLog, useRunLogs, type RecordedLog, type ServedLog } from '../services/runLogs'
import { ApiError } from '../services/api/client'

vi.mock('../services/runLogs', async importOriginal => ({
  ...(await importOriginal<typeof import('../services/runLogs')>()),
  useRunLogs: vi.fn(),
  useRunLog: vi.fn(),
}))

const SHA = 'a'.repeat(64)

function log(overrides: Partial<RecordedLog> = {}): RecordedLog {
  return {
    id: '3-1', verification_id: 3, source: 'test_run', stream: 'stdout', object: 'run-x/stdout',
    recorded_size: 5, recorded_sha256: SHA, openable: true, observed_at: null, ...overrides,
  }
}

function served(overrides: Partial<ServedLog> = {}): ServedLog {
  const text = overrides.text ?? 'hello\nworld'
  return {
    integrity: 'matched', text, blob: new Blob([text]), size: text.length,
    recordedSha256: SHA, observedSha256: SHA, observedBy: 'backend', filename: 'run.log', ...overrides,
  }
}

function given(logs: RecordedLog[], read: { data?: ServedLog; isError?: boolean; error?: unknown }) {
  vi.mocked(useRunLogs).mockReturnValue({ data: { logs }, isLoading: false, isError: false } as never)
  vi.mocked(useRunLog).mockReturnValue({ isLoading: false, isError: false, ...read } as never)
}

function openFirst() {
  render(<RunLogViewer companyId={1} runId={7} />)
  fireEvent.click(screen.getByRole('button', { name: i18n.t('runLogs.open') }))
}

describe('RunLogViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    URL.createObjectURL = vi.fn(() => 'blob:log')
    URL.revokeObjectURL = vi.fn()
  })

  it('shows the hash the backend observed, the log in monospace and a download of the bytes served', () => {
    given([log()], { data: served() })
    openFirst()
    expect(screen.getByText(i18n.t('runLogs.integrity.matched'))).toBeInTheDocument()
    expect(screen.getByTestId('run-log-lines')).toHaveTextContent('hello')
    const download = screen.getByRole('link', { name: i18n.t('runLogs.download') })
    expect(download).toHaveAttribute('href', 'blob:log')
    expect(download).toHaveAttribute('download', 'run.log')
  })

  it('says mismatched as the backend said it', () => {
    given([log()], { data: served({ integrity: 'mismatched', observedSha256: 'b'.repeat(64) }) })
    openFirst()
    expect(screen.getByText(i18n.t('runLogs.integrity.mismatched'))).toBeInTheDocument()
    expect(screen.getByText('b'.repeat(64))).toBeInTheDocument()
  })

  it('searches the lines and pages through them', () => {
    const text = Array.from({ length: LINES_PER_PAGE + 10 }, (_, i) => `line ${i + 1}`).join('\n')
    given([log()], { data: served({ text }) })
    openFirst()
    expect(screen.getByText(i18n.t('runLogs.page', { page: 1, pages: 2 }))).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: i18n.t('runLogs.next') }))
    expect(screen.getByTestId('run-log-lines')).toHaveTextContent(`line ${LINES_PER_PAGE + 10}`)
    fireEvent.change(screen.getByLabelText(i18n.t('runLogs.search')), { target: { value: 'line 7' } })
    expect(screen.getByText(i18n.t('runLogs.page', { page: 1, pages: 1 }))).toBeInTheDocument()
    expect(screen.getByTestId('run-log-lines')).not.toHaveTextContent('line 8')
  })

  it.each([
    ['missing', 410],
    ['unavailable', 503],
  ] as const)('a %s log says so from the refusal, never matched', (integrity, status) => {
    const error = new ApiError(status, 'x', 'gone', { message: 'gone', integrity: { status: integrity, reason: 'gone', recorded_sha256: SHA } })
    given([log()], { isError: true, error })
    openFirst()
    expect(screen.getByText(i18n.t(`runLogs.integrity.${integrity}`))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('runLogs.integrity.matched'))).not.toBeInTheDocument()
  })

  it('a log that was not kept cannot be opened and says why', () => {
    given([log({ openable: false, reason: 'full logs are turned off' })], {})
    render(<RunLogViewer companyId={1} runId={7} />)
    expect(screen.queryByRole('button', { name: i18n.t('runLogs.open') })).not.toBeInTheDocument()
    expect(screen.getByText(i18n.t('runLogs.notKept', { reason: 'full logs are turned off' }))).toBeInTheDocument()
  })
})
