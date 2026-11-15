import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { StickyNote } from 'lucide-react'
import { useAddBoardNote, useBoardNotes, useMarkBoardNoteWrong } from '../hooks/useBoardNotes'
import { BOARD_NOTE_KINDS, errorDetail, type BoardNote, type BoardNoteKind } from '../services/api'
import { SkeletonBlock } from './Skeleton'

const KIND_STYLES: Record<BoardNoteKind, string> = {
  decision: 'border-emerald-700/60 text-emerald-300',
  interface_change: 'border-sky-700/60 text-sky-300',
  schema_change: 'border-violet-700/60 text-violet-300',
  gotcha: 'border-amber-700/60 text-amber-300',
  question: 'border-surface-600 text-gray-300',
}
// While work is moving, notes appear without a reload.
const REFRESH_MS = 10_000

function splitList(value: string): string[] {
  return value.split(',').map(part => part.trim()).filter(Boolean)
}

/**
 * The sprint board (issue #439): the short notes the sprint's tasks leave each
 * other. A note is its author's claim; beside each file it names is what the
 * Verifier's records observed. A Manager adds a note or marks one wrong.
 */
export function BoardNotes({ sprintId, canManage, live = false }: {
  sprintId: number
  canManage: boolean
  live?: boolean
}) {
  const { t } = useTranslation()
  const [showInactive, setShowInactive] = useState(false)
  const query = useBoardNotes(sprintId, live ? REFRESH_MS : false)
  const all = query.data?.notes ?? []
  const notes = showInactive ? all : all.filter(note => note.status === 'active')
  const metrics = query.data?.metrics

  return (
    <section aria-labelledby="board-notes-title" className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h2 id="board-notes-title" className="flex items-center gap-2 text-sm font-semibold text-white">
            <StickyNote size={15} aria-hidden="true" /> {t('boardNotes.title')}
          </h2>
          <p className="text-xs text-gray-500">{t('boardNotes.claimsNote')}</p>
        </div>
        <label className="flex items-center gap-2 text-xs text-gray-400">
          <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} />
          {t('boardNotes.showInactive')}
        </label>
      </div>

      {metrics && (
        <p className="text-xs text-gray-400" data-testid="board-notes-metrics">
          {t('boardNotes.metrics', {
            active: metrics.notes.active,
            wrong: metrics.notes.marked_wrong,
            contradictions: metrics.contradiction_rework,
            rework: metrics.rework,
          })}
        </p>
      )}

      {canManage && <AddNoteForm sprintId={sprintId} />}

      {query.isLoading ? (
        <div role="status" aria-label={t('boardNotes.loading')}>
          <SkeletonBlock className="h-16" />
        </div>
      ) : query.isError ? (
        <div role="alert" className="alert-error flex flex-wrap items-center gap-2 text-sm">
          <span>{t('boardNotes.error')}</span>
          <button type="button" onClick={() => query.refetch()} className="underline">{t('common.retry')}</button>
        </div>
      ) : notes.length === 0 ? (
        <p className="text-sm text-gray-400">{t('boardNotes.empty')}</p>
      ) : (
        <ol className="space-y-2" aria-label={t('boardNotes.listLabel')}>
          {notes.map(note => (
            <NoteItem key={note.id} note={note} sprintId={sprintId} canManage={canManage} />
          ))}
        </ol>
      )}
    </section>
  )
}

function AddNoteForm({ sprintId }: { sprintId: number }) {
  const { t } = useTranslation()
  const add = useAddBoardNote(sprintId)
  const [kind, setKind] = useState<BoardNoteKind>('decision')
  const [text, setText] = useState('')
  const [files, setFiles] = useState('')
  const [requirements, setRequirements] = useState('')

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!text.trim()) return
    add.mutate(
      { kind, text: text.trim(), files: splitList(files), requirements: splitList(requirements) },
      { onSuccess: () => { setText(''); setFiles(''); setRequirements('') } },
    )
  }

  return (
    <form onSubmit={submit} className="card space-y-2 p-3" aria-label={t('boardNotes.addTitle')}>
      <div className="flex flex-wrap gap-2">
        <label className="text-xs text-gray-400">
          <span className="input-label">{t('boardNotes.kindLabel')}</span>
          <select value={kind} onChange={e => setKind(e.target.value as BoardNoteKind)} className="select py-1 text-xs">
            {BOARD_NOTE_KINDS.map(value => (
              <option key={value} value={value}>{t(`boardNotes.kinds.${value}`)}</option>
            ))}
          </select>
        </label>
        <label className="min-w-0 flex-1 text-xs text-gray-400">
          <span className="input-label">{t('boardNotes.filesLabel')}</span>
          <input value={files} onChange={e => setFiles(e.target.value)} className="input w-full font-mono text-xs"
            placeholder={t('boardNotes.filesPlaceholder')} />
        </label>
        <label className="min-w-0 flex-1 text-xs text-gray-400">
          <span className="input-label">{t('boardNotes.requirementsLabel')}</span>
          <input value={requirements} onChange={e => setRequirements(e.target.value)} className="input w-full font-mono text-xs"
            placeholder={t('boardNotes.requirementsPlaceholder')} />
        </label>
      </div>
      <label className="block text-xs text-gray-400">
        <span className="input-label">{t('boardNotes.textLabel')}</span>
        <textarea value={text} onChange={e => setText(e.target.value)} maxLength={500} rows={2} className="input w-full text-sm" />
      </label>
      {add.isError && <div role="alert" className="alert-error text-sm">{errorDetail(add.error)}</div>}
      <button type="submit" className="btn btn-primary text-xs" disabled={add.isPending || !text.trim()}>
        {t('boardNotes.add')}
      </button>
    </form>
  )
}

function NoteItem({ note, sprintId, canManage }: { note: BoardNote; sprintId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const mark = useMarkBoardNoteWrong(sprintId)
  const [marking, setMarking] = useState(false)
  const [reason, setReason] = useState('')
  const author = note.author_role === 'person'
    ? (note.author_name ?? t('boardNotes.aPerson'))
    : note.author_role
  const otherLinks = (['symbols', 'requirements', 'modules'] as const).filter(kind => note.links[kind].length > 0)

  return (
    <li className={`card p-3 ${note.status === 'active' ? '' : 'opacity-70'}`}>
      <div className="mb-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <span className="text-gray-500">#{note.id}</span>
        <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${KIND_STYLES[note.kind] ?? KIND_STYLES.question}`}>
          {t(`boardNotes.kinds.${note.kind}`, { defaultValue: note.kind })}
        </span>
        <span className="font-semibold text-white">{author}</span>
        {note.author_task_id !== null && (
          <Link to={`/sprints/${sprintId}/tasks/${note.author_task_id}`} className="text-brand-300 hover:underline">
            {t('boardNotes.task', { id: note.author_task_id })}
          </Link>
        )}
        {note.status !== 'active' && (
          <span className="rounded-full border border-red-700/60 px-2 py-0.5 text-[10px] text-red-300">
            {t(`boardNotes.status.${note.status}`)}
          </span>
        )}
        {note.created_at && (
          <time dateTime={note.created_at} className="ml-auto text-gray-500">
            {new Date(note.created_at).toLocaleString()}
          </time>
        )}
      </div>
      <p className="text-sm text-gray-200">{note.text}</p>
      {note.links.files.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-xs" aria-label={t('boardNotes.filesLabel')}>
          {note.links.files.map(path => {
            const seen = note.observed.files[path]
            return (
              <li key={path} className="flex flex-wrap gap-x-2">
                <code className="text-gray-300">{path}</code>
                <span className={seen ? 'text-emerald-300' : 'text-gray-500'}>
                  {seen
                    ? t('boardNotes.observed', { run: seen.run_id })
                    : t('boardNotes.notObserved')}
                </span>
              </li>
            )
          })}
        </ul>
      )}
      {otherLinks.length > 0 && (
        <p className="mt-1 text-xs text-gray-400">
          {otherLinks.map(kind => `${t(`boardNotes.links.${kind}`)}: ${note.links[kind].join(', ')}`).join(' · ')}
        </p>
      )}
      {note.supersedes_id !== null && (
        <p className="mt-1 text-xs text-gray-500">{t('boardNotes.supersedes', { id: note.supersedes_id })}</p>
      )}
      {note.status === 'marked_wrong' && (
        <p className="mt-1 text-xs text-red-300">
          {t('boardNotes.markedWrongBy', { by: note.status_by ?? '' })}
          {note.status_reason ? ` — ${note.status_reason}` : ''}
        </p>
      )}
      {canManage && note.status !== 'marked_wrong' && (
        marking ? (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input value={reason} onChange={e => setReason(e.target.value)} maxLength={300}
              aria-label={t('boardNotes.reasonLabel')} placeholder={t('boardNotes.reasonLabel')} className="input min-w-0 flex-1 text-xs" />
            <button type="button" className="btn btn-ghost text-xs" disabled={mark.isPending}
              onClick={() => mark.mutate({ id: note.id, reason: reason.trim() }, { onSuccess: () => setMarking(false) })}>
              {t('boardNotes.confirmWrong')}
            </button>
            <button type="button" className="btn btn-ghost text-xs" onClick={() => setMarking(false)}>{t('common.cancel')}</button>
          </div>
        ) : (
          <button type="button" className="mt-2 text-xs text-gray-400 underline" onClick={() => setMarking(true)}>
            {t('boardNotes.markWrong')}
          </button>
        )
      )}
      {mark.isError && <div role="alert" className="alert-error mt-1 text-xs">{errorDetail(mark.error)}</div>}
    </li>
  )
}
