/**
 * The integration's diff as the workspace daemon read it, file by file, with
 * the comments under the lines they are about and, for a Manager, a form on
 * any line the diff shows. The backend anchors each comment to the daemon's
 * diff again and keeps the file's hash on that side; nothing here is trusted.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, Loader2, MessageSquarePlus } from 'lucide-react'
import { errorDetail } from '../services/api'
import type { CommentSide } from '../services/runReviews'
import {
  type IntegrationComment,
  type IntegrationDiffFile,
  useAddIntegrationComment,
  useResolveIntegrationComment,
} from '../services/integrationReviews'
import { patchRows, type PatchRow } from '../lib/patch'

function target(row: PatchRow): { side: CommentSide; line: number } | null {
  if (row.kind === 'del' && row.oldLine !== null) return { side: 'old', line: row.oldLine }
  if ((row.kind === 'add' || row.kind === 'context') && row.newLine !== null) return { side: 'new', line: row.newLine }
  return null
}

const ROW_CLASS: Record<PatchRow['kind'], string> = {
  hunk: 'bg-surface-800 text-gray-400',
  add: 'bg-emerald-950/50 text-emerald-200',
  del: 'bg-red-950/50 text-red-200',
  context: 'text-gray-300',
  note: 'text-gray-500 italic',
}

export function IntegrationCommentList({ sprintId, comments, canReview }: { sprintId: number; comments: IntegrationComment[]; canReview: boolean }) {
  const { t } = useTranslation()
  const resolve = useResolveIntegrationComment(sprintId)
  return (
    <ul className="space-y-2">
      {comments.map(comment => (
        <li key={comment.id} className={`rounded border border-surface-700 bg-surface-800 p-2 font-sans text-sm ${comment.resolved_at ? 'opacity-60' : ''}`}>
          <div className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
            <span className="font-semibold text-gray-200">{comment.author_name}</span>
            {comment.review_id !== null && <span>{t('integrationReview.comment.inReview', { id: comment.review_id })}</span>}
            {comment.review_id === null && !comment.resolved_at && <span className="text-amber-300">{t('integrationReview.comment.pending')}</span>}
            {comment.resolved_at && <span>{t('integrationReview.comment.resolved')}</span>}
            {canReview && !comment.resolved_at && comment.review_id === null && (
              <button type="button" onClick={() => resolve.mutate(comment.id)} disabled={resolve.isPending}
                className="ml-auto flex items-center gap-1 text-gray-400 hover:text-gray-200">
                <CheckCircle2 size={12} aria-hidden="true" />
                {t('integrationReview.comment.resolve')}
              </button>
            )}
          </div>
          <p className="mt-1 whitespace-pre-wrap text-gray-100">{comment.body}</p>
        </li>
      ))}
    </ul>
  )
}

export function IntegrationCommentForm({ sprintId, integrationId, path, side, line, onDone }: {
  sprintId: number; integrationId: number; path: string | null; side: CommentSide; line: number | null; onDone?: () => void
}) {
  const { t } = useTranslation()
  const add = useAddIntegrationComment(sprintId)
  const [body, setBody] = useState('')
  const send = (event: React.FormEvent) => {
    event.preventDefault()
    if (!body.trim()) return
    add.mutate({ integration_id: integrationId, path, side, line, body: body.trim() }, { onSuccess: () => { setBody(''); onDone?.() } })
  }
  const label = line === null ? t('integrationReview.comment.labelGeneral') : t('integrationReview.comment.label', { path, line })
  return (
    <form onSubmit={send} className="space-y-2 font-sans">
      <label className="block text-xs text-gray-400">
        {label}
        <textarea value={body} onChange={event => setBody(event.target.value)} rows={2} maxLength={4000}
          className="mt-1 w-full rounded border border-surface-700 bg-surface-900 p-2 text-sm text-gray-100" />
      </label>
      <div className="flex items-center gap-2">
        <button type="submit" disabled={add.isPending || !body.trim()} className="btn btn-primary btn-sm flex items-center gap-1.5">
          {add.isPending && <Loader2 size={13} className="animate-spin" />}
          {t('integrationReview.comment.submit')}
        </button>
        {onDone && <button type="button" onClick={onDone} className="btn btn-ghost btn-sm">{t('common.cancel')}</button>}
      </div>
      {add.isError && <p role="alert" className="text-sm text-red-300">{errorDetail(add.error)}</p>}
    </form>
  )
}

function DiffFile({ sprintId, integrationId, file, comments, canReview }: {
  sprintId: number; integrationId: number; file: IntegrationDiffFile; comments: IntegrationComment[]; canReview: boolean
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState<string | null>(null)
  const rows = patchRows(file.patch)
  const at = (side: CommentSide, line: number) => comments.filter(c => c.path === file.path && c.side === side && c.line === line)
  return (
    <details open className="rounded border border-surface-700">
      <summary className="cursor-pointer px-2 py-1 text-xs text-gray-300">
        <code>{file.path}</code>{' '}
        <span className="text-gray-500">{t(`integrationReview.change.${file.change}`, { defaultValue: file.change })} {t('integrationReview.counts', { additions: file.additions, deletions: file.deletions })}</span>
      </summary>
      {file.omitted ? (
        <p className="px-2 py-1 text-xs text-gray-500">{t(`integrationReview.omitted.${file.omitted}`, { defaultValue: file.omitted })}</p>
      ) : (
        <table className="w-full font-mono text-[11px]">
          <tbody>
            {rows.map((row, index) => {
              const where = target(row)
              const key = where ? `${where.side}:${where.line}` : null
              const thread = where ? at(where.side, where.line) : []
              return [
                <tr key={`r${index}`} className={ROW_CLASS[row.kind]}>
                  <td className="w-10 select-none pr-1 text-right text-gray-600">{row.oldLine ?? ''}</td>
                  <td className="w-10 select-none pr-1 text-right text-gray-600">{row.newLine ?? ''}</td>
                  <td className="w-5">
                    {canReview && where && (
                      <button type="button" onClick={() => setOpen(key)} aria-label={t('integrationReview.comment.add', { path: file.path, line: where.line })}
                        className="text-gray-500 hover:text-brand-300">
                        <MessageSquarePlus size={11} aria-hidden="true" />
                      </button>
                    )}
                  </td>
                  <td className="whitespace-pre-wrap break-all">{row.text}</td>
                </tr>,
                (thread.length > 0 || open === key) && where ? (
                  <tr key={`c${index}`}>
                    <td colSpan={4} className="space-y-2 p-2">
                      {thread.length > 0 && <IntegrationCommentList sprintId={sprintId} comments={thread} canReview={canReview} />}
                      {open === key && (
                        <IntegrationCommentForm sprintId={sprintId} integrationId={integrationId} path={file.path}
                          side={where.side} line={where.line} onDone={() => setOpen(null)} />
                      )}
                    </td>
                  </tr>
                ) : null,
              ]
            })}
          </tbody>
        </table>
      )}
    </details>
  )
}

export function IntegrationDiffView({ sprintId, integrationId, files, comments, canReview }: {
  sprintId: number; integrationId: number; files: IntegrationDiffFile[]; comments: IntegrationComment[]; canReview: boolean
}) {
  return (
    <div className="space-y-2">
      {files.map(file => (
        <DiffFile key={file.path} sprintId={sprintId} integrationId={integrationId} file={file}
          comments={comments} canReview={canReview} />
      ))}
    </div>
  )
}
