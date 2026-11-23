import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CircleX, MessageSquarePlus, RotateCcw } from 'lucide-react'
import { useCurrentRole, useUpdateBacklogItem } from '../hooks/useQueries'
import { errorDetail, type BacklogItem } from '../services/api'
import { CountPill, Panel } from './ui'
import { commentAuthor } from '../lib/commentAuthor'

/** The thread's comments a person wrote (author ``user``), newest last. */
export function personComments(task: BacklogItem) {
  return (task.comments ?? []).filter(comment => comment.author === 'user' && comment.text?.trim())
}

/** A task a person can discard: no worker holds it (services/loop/board_moves.py). */
export const DISCARDABLE: BacklogItem['status'][] = ['todo', 'blocked']

/**
 * A person's comments on a task, and the form a Manager writes one with
 * (`PATCH /v1/backlog/{id}`). The same text is the reason when the task is
 * discarded; a discarded task can be reopened to To do, which starts it.
 */
export function TaskComments({ task }: { task: BacklogItem }) {
  const { t } = useTranslation()
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const update = useUpdateBacklogItem()
  const [text, setText] = useState('')
  const comments = personComments(task)
  const trimmed = text.trim()
  const discarded = task.status === 'discarded'

  const send = (data: { comment?: string; status?: string }) =>
    update.mutate({ itemId: task.id, data }, { onSuccess: () => setText('') })

  return (
    <Panel
      title={<span className="flex items-center gap-1.5"><MessageSquarePlus size={13} aria-hidden="true" className="text-gray-400" />{t('taskComments.title')}</span>}
      count={comments.length > 0 ? <CountPill count={comments.length} label={t('taskComments.count', { count: comments.length })} /> : undefined}>
      {discarded && (
        <p className="flex items-center gap-1.5 border-b border-surface-700 px-3 py-2 text-xs text-gray-300">
          <CircleX size={12} aria-hidden="true" className="text-gray-500" /> {t('taskComments.discardedNote')}
        </p>
      )}
      {comments.length === 0 ? (
        <p className="px-3 py-3 text-xs text-gray-400">{t('taskComments.empty')}</p>
      ) : (
        <ul className="data-list" aria-label={t('taskComments.listLabel')}>
          {comments.map((comment, index) => (
            <li key={`${comment.created_at}-${index}`} className="px-3 py-2">
              <p className="whitespace-pre-wrap text-[13px] text-gray-200">{comment.text}</p>
              <p className="text-meta mt-0.5 tabular-nums">
                {commentAuthor(comment, t)}
                {comment.created_at && ` · ${comment.created_at.replace('T', ' ').slice(0, 16)}`}
              </p>
            </li>
          ))}
        </ul>
      )}
      {canManage && (
        <form
          className="space-y-2 border-t border-surface-700 p-3"
          aria-label={t('taskComments.formLabel')}
          onSubmit={event => { event.preventDefault(); if (trimmed) send({ comment: trimmed }) }}
        >
          <label className="block">
            <span className="text-meta mb-1 block">{t('taskComments.label')}</span>
            <textarea
              value={text}
              onChange={event => setText(event.target.value)}
              rows={3}
              maxLength={4000}
              className="input w-full text-[13px]"
              placeholder={t('taskComments.placeholder')}
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button type="submit" className="btn-tool border border-surface-700" disabled={update.isPending || !trimmed}>
              {t('taskComments.add')}
            </button>
            {DISCARDABLE.includes(task.status) && (
              <button
                type="button"
                className="btn-tool border border-surface-700 text-gray-300"
                disabled={update.isPending || !trimmed}
                title={trimmed ? undefined : t('taskComments.discardNeedsReason')}
                onClick={() => send({ status: 'discarded', comment: trimmed })}
              >
                <CircleX size={12} aria-hidden="true" /> {t('taskComments.discard')}
              </button>
            )}
            {discarded && (
              <button
                type="button"
                className="btn-tool border border-surface-700"
                disabled={update.isPending}
                onClick={() => send(trimmed ? { status: 'todo', comment: trimmed } : { status: 'todo' })}
              >
                <RotateCcw size={12} aria-hidden="true" /> {t('taskComments.reopen')}
              </button>
            )}
          </div>
          {DISCARDABLE.includes(task.status) && <p className="text-meta">{t('taskComments.discardHelp')}</p>}
          {update.isError && <p role="alert" className="text-xs text-red-300">{errorDetail(update.error)}</p>}
        </form>
      )}
    </Panel>
  )
}
