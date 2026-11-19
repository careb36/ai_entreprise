import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowUpToLine, Loader2, ShieldCheck, ShieldQuestion } from 'lucide-react'
import { StatusBadge } from './ui'
import { PIECE_BYTES, refusalOf, useRunLogPieces, type RecordedLog, type ServedPiece } from '../services/runLogs'

/** A log larger than one piece is read from its tail, piece by piece. */
export function readInPieces(log: RecordedLog): boolean {
  return typeof log.recorded_size === 'number' && log.recorded_size > PIECE_BYTES
}

/** The pieces read so far, from the earliest byte to the last. */
function joined(pieces: ServedPiece[]): { text: string; start: number; end: number } {
  const ordered = [...pieces].reverse()
  const length = ordered.reduce((sum, piece) => sum + piece.bytes.length, 0)
  const bytes = new Uint8Array(length)
  let at = 0
  for (const piece of ordered) {
    bytes.set(piece.bytes, at)
    at += piece.bytes.length
  }
  return {
    text: new TextDecoder().decode(bytes),
    start: ordered.length ? ordered[0].start : 0,
    end: ordered.length ? ordered[ordered.length - 1].end : -1,
  }
}

/**
 * A large full log (issue #315), read from its tail: the last piece first and
 * earlier ones on demand, each a byte range the backend serves as
 * `unverified` (reason `partial`), since a piece cannot be checked against
 * the whole log's sha256. Reading the whole log is the only way to a
 * verified result, so it is offered beside the pieces.
 */
export function RunLogPieces({ companyId, runId, log, onReadWhole }: {
  companyId: number
  runId: number
  log: RecordedLog
  onReadWhole: () => void
}) {
  const { t } = useTranslation()
  const total = log.recorded_size ?? 0
  const query = useRunLogPieces(companyId, runId, log.id, total)
  const pieces = useMemo(() => query.data?.pages ?? [], [query.data])
  const shown = useMemo(() => joined(pieces), [pieces])
  const verified = pieces.length === 1 && pieces[0].verified

  if (query.isLoading) {
    return <p role="status" className="flex items-center gap-2 p-3 text-xs text-gray-400"><Loader2 size={14} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />{t('runLogs.pieces.reading')}</p>
  }
  if (query.isError && pieces.length === 0) {
    const refused = refusalOf(query.error)
    return (
      <div className="space-y-2 p-3">
        <StatusBadge icon={ShieldQuestion} tone="neutral" label={t(`runLogs.integrity.${refused.integrity}`)} />
        <p role="alert" className="text-xs text-gray-300">{t('runLogs.pieces.refused')} {refused.reason}</p>
        <button type="button" className="btn-tool" onClick={onReadWhole}>{t('runLogs.pieces.readWhole')}</button>
      </div>
    )
  }

  return (
    <div className="space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        {verified
          ? <StatusBadge icon={ShieldCheck} tone="success" label={t('runLogs.integrity.matched')} />
          : <StatusBadge icon={ShieldQuestion} tone="warning" label={t('runLogs.integrity.unverified')} />}
        <span className="text-meta tabular-nums text-gray-400">
          {t('runLogs.pieces.showing', { start: shown.start, end: shown.end, total })}
        </span>
        <button type="button" className="btn-tool ml-auto" onClick={onReadWhole}>{t('runLogs.pieces.readWhole')}</button>
      </div>
      {!verified && <p className="text-meta text-gray-400">{t('runLogs.pieces.partialHint')}</p>}
      {query.hasNextPage && (
        <button type="button" className="btn-tool" disabled={query.isFetchingNextPage} onClick={() => query.fetchNextPage()}>
          {query.isFetchingNextPage
            ? <Loader2 size={14} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
            : <ArrowUpToLine size={14} aria-hidden="true" />}
          {t('runLogs.pieces.earlier', { bytes: Math.min(PIECE_BYTES, shown.start) })}
        </button>
      )}
      {query.isError && <p role="alert" className="text-xs text-red-300">{t('runLogs.pieces.earlierError')}</p>}
      <pre data-testid="run-log-piece" className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-all rounded-md border border-surface-700 bg-surface-950 p-2 font-mono text-xs leading-5 text-gray-200">
        {shown.text}
      </pre>
    </div>
  )
}
