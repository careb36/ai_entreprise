/**
 * A run's full logs (issue #315): `GET /v1/agent_runs/{id}/logs` lists what
 * the run's verifications recorded, and `GET .../logs/{log_id}` reads one
 * back. The backend computes the sha256 over the bytes it serves and compares
 * it with the one it recorded; the result arrives in the `X-Log-*` headers (or
 * a refusal's `detail.integrity`) and is never computed or accepted here.
 */
import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { api } from './api'
import { ApiError, fetchWithHeaders } from './api/client'

/** `unverified`: a piece of the log, which cannot be checked against the whole log's sha256. */
export type LogIntegrity = 'matched' | 'mismatched' | 'missing' | 'unavailable' | 'unverified'

/** Bytes per piece when a large log is read from its tail. */
export const PIECE_BYTES = 256 * 1024

export interface RecordedLog {
  id: string
  verification_id: number
  /** Where the record sits in the verification's evidence (`test_run`, `app_run`, …). */
  source: string
  stream: string | null
  object: string | null
  recorded_size?: number | null
  recorded_sha256?: string | null
  truncated_by_daemon?: boolean
  openable: boolean
  reason?: string
  observed_at: string | null
}

export interface ServedLog {
  integrity: LogIntegrity
  text: string
  blob: Blob
  size: number
  recordedSha256: string | null
  observedSha256: string | null
  observedBy: string | null
  filename: string
}

/** A log the backend could not serve, with the integrity it observed. */
export interface RefusedLog {
  integrity: LogIntegrity
  reason: string
  recordedSha256: string | null
}

/**
 * A byte range of a log (`206 Partial Content`). The backend serves it as
 * `unverified` with the reason `partial`: only the full read is ever checked.
 */
export interface ServedPiece {
  integrity: LogIntegrity
  verified: boolean
  unverifiedReason: string | null
  bytes: Uint8Array
  start: number
  end: number
  total: number
  recordedSha256: string | null
}

const INTEGRITIES: readonly LogIntegrity[] = ['matched', 'mismatched', 'missing', 'unavailable', 'unverified']

function asIntegrity(value: unknown): LogIntegrity {
  return INTEGRITIES.includes(value as LogIntegrity) ? (value as LogIntegrity) : 'unavailable'
}

/** The integrity a refusal's `detail` carries; anything else is unavailable, never matched. */
export function refusalOf(error: unknown): RefusedLog {
  const detail = error instanceof ApiError ? error.data : undefined
  const integrity = (detail as { integrity?: { status?: unknown; reason?: unknown; recorded_sha256?: unknown } } | undefined)?.integrity
  return {
    integrity: integrity ? asIntegrity(integrity.status) : 'unavailable',
    reason: typeof integrity?.reason === 'string' ? integrity.reason : error instanceof Error ? error.message : String(error),
    recordedSha256: typeof integrity?.recorded_sha256 === 'string' ? integrity.recorded_sha256 : null,
  }
}

export const runLogsApi = {
  list: (companyId: number, runId: number) =>
    api.get<{ logs: RecordedLog[] }>(`/v1/agent_runs/${runId}/logs`, { company_id: companyId }),
  read: async (companyId: number, runId: number, logId: string): Promise<ServedLog> => {
    const query = new URLSearchParams({ company_id: String(companyId), download: 'true' })
    const { blob, headers } = await fetchWithHeaders(`/v1/agent_runs/${runId}/logs/${encodeURIComponent(logId)}?${query}`)
    const disposition = headers.get('Content-Disposition') ?? ''
    const named = /filename="?([^";]+)"?/.exec(disposition)
    return {
      // Only a value the backend sent counts; a missing header is unavailable.
      integrity: asIntegrity(headers.get('X-Log-Integrity')),
      text: await blob.text(),
      blob,
      size: Number(headers.get('X-Log-Size') ?? blob.size),
      recordedSha256: headers.get('X-Log-Sha256-Recorded'),
      observedSha256: headers.get('X-Log-Sha256-Observed'),
      observedBy: headers.get('X-Log-Observed-By'),
      filename: named ? named[1] : `run-${runId}-${logId}.log`,
    }
  },
  /** Bytes `offset`..`offset + limit - 1` of a log; never verified, whatever arrives. */
  readPiece: async (companyId: number, runId: number, logId: string, offset: number, limit: number): Promise<ServedPiece> => {
    const query = new URLSearchParams({ company_id: String(companyId), offset: String(offset), limit: String(limit) })
    const { blob, headers } = await fetchWithHeaders(`/v1/agent_runs/${runId}/logs/${encodeURIComponent(logId)}?${query}`)
    const bytes = new Uint8Array(await blob.arrayBuffer())
    const range = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(headers.get('Content-Range') ?? '')
    const total = range ? Number(range[3]) : Number(headers.get('X-Log-Size') ?? bytes.length)
    const start = range ? Number(range[1]) : 0
    const integrity = asIntegrity(headers.get('X-Log-Integrity'))
    return {
      integrity,
      // A piece is verified only if the backend served the whole log and said so.
      verified: !range && integrity === 'matched' && headers.get('X-Log-Verified') === 'true',
      unverifiedReason: headers.get('X-Log-Unverified-Reason'),
      bytes,
      start,
      end: start + bytes.length - 1,
      total,
      recordedSha256: headers.get('X-Log-Sha256-Recorded'),
    }
  },
}

/** The range before a piece that starts at `start`, or `undefined` at the start of the log. */
export function earlierPiece(start: number): { offset: number; limit: number } | undefined {
  if (start <= 0) return undefined
  const offset = Math.max(0, start - PIECE_BYTES)
  return { offset, limit: start - offset }
}

/** A large log read from its tail; each further page is the piece before the last one read. */
export function useRunLogPieces(companyId: number, runId: number, logId: string, total: number) {
  const tail = Math.max(0, total - PIECE_BYTES)
  return useInfiniteQuery({
    queryKey: ['agent-run', companyId, runId, 'logs', logId, 'pieces'] as const,
    queryFn: ({ pageParam }) => runLogsApi.readPiece(companyId, runId, logId, pageParam.offset, pageParam.limit),
    initialPageParam: { offset: tail, limit: total - tail },
    getNextPageParam: (last: ServedPiece) => earlierPiece(last.start),
    retry: false,
    gcTime: 0,
    staleTime: 0,
  })
}

export function useRunLogs(companyId: number, runId: number) {
  return useQuery({
    queryKey: ['agent-run', companyId, runId, 'logs'] as const,
    queryFn: () => runLogsApi.list(companyId, runId),
  })
}

/** Read only when a person opens the log; read again each time (no cache of the bytes). */
export function useRunLog(companyId: number, runId: number, logId: string | null) {
  return useQuery({
    queryKey: ['agent-run', companyId, runId, 'logs', logId] as const,
    queryFn: () => runLogsApi.read(companyId, runId, logId as string),
    enabled: logId !== null,
    retry: false,
    gcTime: 0,
    staleTime: 0,
  })
}
