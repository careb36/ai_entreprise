import { fireEvent, render, screen } from '@testing-library/react'
import i18n from 'i18next'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RunLogViewer } from './RunLogViewer'
import { earlierPiece, PIECE_BYTES, runLogsApi, useRunLog, useRunLogPieces, useRunLogs, type RecordedLog, type ServedPiece } from '../services/runLogs'
import { fetchWithHeaders } from '../services/api/client'

vi.mock('../services/runLogs', async importOriginal => ({
  ...(await importOriginal<typeof import('../services/runLogs')>()),
  useRunLogs: vi.fn(),
  useRunLog: vi.fn(),
  useRunLogPieces: vi.fn(),
}))

vi.mock('../services/api/client', async importOriginal => ({
  ...(await importOriginal<typeof import('../services/api/client')>()),
  fetchWithHeaders: vi.fn(),
}))

const SHA = 'a'.repeat(64)
const BIG = PIECE_BYTES * 3

function log(overrides: Partial<RecordedLog> = {}): RecordedLog {
  return {
    id: '3-1', verification_id: 3, source: 'test_run', stream: 'stdout', object: 'run-x/stdout',
    recorded_size: BIG, recorded_sha256: SHA, openable: true, observed_at: null, ...overrides,
  }
}

function piece(text: string, start: number): ServedPiece {
  const bytes = new TextEncoder().encode(text)
  return {
    integrity: 'unverified', verified: false, unverifiedReason: 'partial', bytes, start,
    end: start + bytes.length - 1, total: BIG, recordedSha256: SHA,
  }
}

function given(logs: RecordedLog[], pages: ServedPiece[], extra: Record<string, unknown> = {}) {
  const fetchNextPage = vi.fn()
  vi.mocked(useRunLogs).mockReturnValue({ data: { logs }, isLoading: false, isError: false } as never)
  vi.mocked(useRunLog).mockReturnValue({ isLoading: true, isError: false } as never)
  vi.mocked(useRunLogPieces).mockReturnValue({
    data: { pages }, isLoading: false, isError: false, hasNextPage: pages.length > 0 && pages[pages.length - 1].start > 0,
    isFetchingNextPage: false, fetchNextPage, ...extra,
  } as never)
  render(<RunLogViewer companyId={1} runId={7} />)
  fireEvent.click(screen.getByRole('button', { name: i18n.t('runLogs.open') }))
  return fetchNextPage
}

describe('RunLogPieces', () => {
  beforeEach(() => vi.clearAllMocks())

  it('reads a large log from its tail and never calls a piece verified', () => {
    given([log()], [piece('the last lines\n', BIG - 15)])
    expect(useRunLogPieces).toHaveBeenCalledWith(1, 7, '3-1', BIG)
    expect(screen.getByTestId('run-log-piece')).toHaveTextContent('the last lines')
    expect(screen.getByText(i18n.t('runLogs.integrity.unverified'))).toBeInTheDocument()
    expect(screen.queryByText(i18n.t('runLogs.integrity.matched'))).not.toBeInTheDocument()
    expect(screen.getByText(i18n.t('runLogs.pieces.partialHint'))).toBeInTheDocument()
  })

  it('fetches the earlier piece on demand and shows the pieces in byte order', () => {
    const fetchNextPage = given([log()], [piece('tail\n', BIG - 5), piece('middle ', BIG - 12)])
    expect(screen.getByTestId('run-log-piece').textContent).toBe('middle tail\n')
    fireEvent.click(screen.getByRole('button', { name: i18n.t('runLogs.pieces.earlier', { bytes: PIECE_BYTES }) }))
    expect(fetchNextPage).toHaveBeenCalled()
  })

  it('offers nothing earlier once the start of the log is read', () => {
    given([log()], [piece('all of it', 0)])
    expect(screen.queryByRole('button', { name: /earlier/i })).not.toBeInTheDocument()
  })

  it('reading the whole log goes to the full read, which is the only verified one', () => {
    given([log()], [piece('tail', BIG - 4)])
    expect(useRunLog).not.toHaveBeenCalledWith(1, 7, '3-1')
    fireEvent.click(screen.getByRole('button', { name: i18n.t('runLogs.pieces.readWhole') }))
    expect(useRunLog).toHaveBeenCalledWith(1, 7, '3-1')
  })

  it('a small log is read whole, as before', () => {
    given([log({ recorded_size: 5 })], [])
    expect(useRunLogPieces).not.toHaveBeenCalled()
    expect(useRunLog).toHaveBeenCalledWith(1, 7, '3-1')
  })
})

describe('runLogsApi.readPiece', () => {
  it('asks for offset and limit and reports a 206 as unverified even if a header says matched', async () => {
    const headers = new Headers({
      'Content-Range': `bytes 10-14/${BIG}`, 'X-Log-Integrity': 'matched', 'X-Log-Verified': 'true',
      'X-Log-Sha256-Recorded': SHA,
    })
    vi.mocked(fetchWithHeaders).mockResolvedValue({ blob: new Blob(['hello']), headers })
    const got = await runLogsApi.readPiece(1, 7, '3-1', 10, 5)
    expect(vi.mocked(fetchWithHeaders).mock.calls[0][0]).toContain('offset=10&limit=5')
    expect(got.verified).toBe(false)
    expect([got.start, got.end, got.total]).toEqual([10, 14, BIG])
  })

  it('places each earlier piece before the last one read', () => {
    expect(earlierPiece(0)).toBeUndefined()
    expect(earlierPiece(10)).toEqual({ offset: 0, limit: 10 })
    expect(earlierPiece(PIECE_BYTES + 3)).toEqual({ offset: 3, limit: PIECE_BYTES })
  })
})
