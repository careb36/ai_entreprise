/**
 * Comments on the lines of a run's changed files, as a pull request has
 * them: a thread under the line it is about, a form to add one (Managers),
 * and Resolve. A comment waits for the next submitted review, which carries
 * it to whoever must act (see `ReviewPanel`).
 */
import { createContext, useContext, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, ExternalLink, Loader2 } from 'lucide-react'
import { errorDetail } from '../services/api'
import type { PatchRow } from '../lib/patch'
import {
  type CommentSide,
  type ReviewComment,
  useAddReviewComment,
  useResolveReviewComment,
  useReviewComments,
} from '../services/runReviews'

interface LineReview {
  canComment: boolean
  comments: ReviewComment[]
  companyId: number
  runId: number
}

const LineReviewContext = createContext<LineReview | null>(null)

/** Where a row's comments live: the new line, or the old one for a deleted line. */
export function rowTarget(row: PatchRow): { side: CommentSide; line: number } | null {
  if (row.kind === 'del' && row.oldLine !== null) return { side: 'old', line: row.oldLine }
  if ((row.kind === 'add' || row.kind === 'context') && row.newLine !== null) return { side: 'new', line: row.newLine }
  return null
}

export function targetKey(target: { side: CommentSide; line: number }): string {
  return `${target.side}:${target.line}`
}

export function LineReviewProvider({ companyId, runId, canComment, children }: {
  companyId: number; runId: number; canComment: boolean; children: React.ReactNode
}) {
  const comments = useReviewComments(companyId, runId).data?.comments ?? []
  return <LineReviewContext.Provider value={{ canComment, comments, companyId, runId }}>{children}</LineReviewContext.Provider>
}

/** The comments on one file, keyed by `side:line`; `null` outside a provider. */
export function useFileComments(path: string): { canComment: boolean; byLine: Map<string, ReviewComment[]> } {
  const review = useContext(LineReviewContext)
  const byLine = new Map<string, ReviewComment[]>()
  for (const comment of review?.comments ?? []) {
    if (comment.path !== path || comment.line === null) continue
    const key = targetKey({ side: comment.side, line: comment.line })
    byLine.set(key, [...(byLine.get(key) ?? []), comment])
  }
  return { canComment: review?.canComment ?? false, byLine }
}

/** Where a comment was written, when it was not here: a link to it on its platform. */
function PlatformBadge({ comment }: { comment: ReviewComment }) {
  const { t } = useTranslation()
  const label = t(`review.comment.on.${comment.source}`)
  return (
    <span className="flex items-center gap-1">
      {comment.external_url ? (
        <a href={comment.external_url} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1 text-brand-300 hover:underline">
          {label}
          <ExternalLink size={11} aria-hidden="true" />
        </a>
      ) : label}
      {comment.external_state && <span className="badge bg-gray-800 text-gray-300">{t(`review.comment.state.${comment.external_state}`, { defaultValue: comment.external_state })}</span>}
    </span>
  )
}

export function CommentThread({ comments }: { comments: ReviewComment[] }) {
  const { t } = useTranslation()
  const review = useContext(LineReviewContext)
  const resolve = useResolveReviewComment(review?.companyId ?? 0, review?.runId ?? 0)
  return (
    <ul className="space-y-2">
      {comments.map(comment => (
        <li key={comment.id} className={`rounded border border-surface-700 bg-surface-800 p-2 font-sans text-sm ${comment.resolved_at ? 'opacity-60' : ''}`}>
          <div className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
            <span className="font-semibold text-gray-200">{comment.author_name}</span>
            {comment.source && <PlatformBadge comment={comment} />}
            {comment.review_id !== null && <span>{t('review.comment.inReview', { id: comment.review_id })}</span>}
            {comment.review_id === null && !comment.resolved_at && <span className="text-amber-300">{t('review.comment.pending')}</span>}
            {comment.resolved_at && <span>{t('review.comment.resolved')}</span>}
            {review?.canComment && !comment.resolved_at && comment.review_id === null && (
              <button type="button" onClick={() => resolve.mutate(comment.id)} disabled={resolve.isPending}
                className="ml-auto flex items-center gap-1 text-gray-400 hover:text-gray-200">
                <CheckCircle2 size={12} aria-hidden="true" />
                {t('review.comment.resolve')}
              </button>
            )}
          </div>
          <p className="mt-1 whitespace-pre-wrap text-gray-100">{comment.body}</p>
        </li>
      ))}
    </ul>
  )
}

export function CommentForm({ path, side, line, onDone }: { path: string; side: CommentSide; line: number; onDone: () => void }) {
  const { t } = useTranslation()
  const review = useContext(LineReviewContext)
  const add = useAddReviewComment(review?.companyId ?? 0, review?.runId ?? 0)
  const [body, setBody] = useState('')
  const send = (event: React.FormEvent) => {
    event.preventDefault()
    if (!body.trim()) return
    add.mutate({ path, side, line, body: body.trim() }, { onSuccess: onDone })
  }
  return (
    <form onSubmit={send} className="space-y-2 font-sans">
      <label className="block text-xs text-gray-400">
        {t('review.comment.label', { line })}
        <textarea autoFocus value={body} onChange={event => setBody(event.target.value)} rows={3} maxLength={4000}
          className="mt-1 w-full rounded border border-surface-700 bg-surface-900 p-2 text-sm text-gray-100" />
      </label>
      <div className="flex items-center gap-2">
        <button type="submit" disabled={add.isPending || !body.trim()} className="btn btn-primary btn-sm flex items-center gap-1.5">
          {add.isPending && <Loader2 size={13} className="animate-spin" />}
          {t('review.comment.submit')}
        </button>
        <button type="button" onClick={onDone} className="btn btn-ghost btn-sm">{t('common.cancel')}</button>
      </div>
      {add.isError && <p role="alert" className="text-sm text-red-300">{errorDetail(add.error)}</p>}
    </form>
  )
}
