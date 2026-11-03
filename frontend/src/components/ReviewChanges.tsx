/**
 * "Files changed": what a run changed, the way GitHub and GitLab show a pull
 * request: the files with their additions and deletions, then each file's
 * hunks with the old and new line numbers.
 *
 * The files come from an observation (what the workspace daemon read back
 * from disk, or the pull request's diff); what the producer only declared is
 * listed apart, never shown as a change.
 */
import { Fragment, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, FileDiff, Plus } from 'lucide-react'
import { patchRows, type PatchRow } from '../lib/patch'
import { PublishBranch } from './PublishBranch'
import { CommentForm, CommentThread, LineReviewProvider, rowTarget, targetKey, useFileComments } from './LineComments'
import { type FileChange, type RunChanges, useRunChanges } from '../services/reviewChanges'

/** Past this many rows a file shows a button for the rest, as GitHub does. */
export const ROWS_SHOWN = 400

const CHANGE_BADGES: Record<string, string> = {
  added: 'bg-emerald-900/60 text-emerald-300',
  modified: 'bg-sky-900/60 text-sky-300',
  deleted: 'bg-red-900/60 text-red-300',
  renamed: 'bg-violet-900/60 text-violet-300',
  unchanged: 'bg-gray-800 text-gray-400',
  not_written: 'bg-amber-900/60 text-amber-300',
}

const ROW_STYLES: Record<PatchRow['kind'], string> = {
  add: 'bg-emerald-950/50 text-emerald-100',
  del: 'bg-red-950/50 text-red-100',
  context: 'text-gray-300',
  hunk: 'bg-sky-950/40 text-sky-300',
  note: 'text-gray-500 italic',
}

const MARKERS: Record<PatchRow['kind'], string> = { add: '+', del: '-', context: ' ', hunk: '', note: '' }

function anchor(path: string): string {
  return `file-${path.replace(/[^A-Za-z0-9_-]/g, '-')}`
}

function Counts({ additions, deletions }: { additions: number; deletions: number }) {
  return (
    <span className="shrink-0 font-mono text-xs">
      <span className="text-emerald-400">+{additions}</span>{' '}
      <span className="text-red-400">-{deletions}</span>
    </span>
  )
}

function FileNotice({ file }: { file: FileChange }) {
  const { t } = useTranslation()
  if (file.change === 'not_written') {
    return <p className="p-4 text-sm text-amber-300">{t('review.notWritten', { status: file.status ?? '' })}</p>
  }
  if (file.change === 'unchanged') return <p className="p-4 text-sm text-gray-400">{t('review.unchanged')}</p>
  if (file.omitted) return <p className="p-4 text-sm text-gray-400">{t(`review.omitted.${file.omitted}`)}</p>
  return <p className="p-4 text-sm text-gray-400">{t('review.noHunks')}</p>
}

function FileDiffTable({ path, rows }: { path: string; rows: PatchRow[] }) {
  const { t } = useTranslation()
  const [showAll, setShowAll] = useState(false)
  const [commenting, setCommenting] = useState<string | null>(null)
  const { canComment, byLine } = useFileComments(path)
  const shown = showAll ? rows : rows.slice(0, ROWS_SHOWN)
  const shownKeys = new Set(shown.map(row => rowTarget(row)).filter(target => target !== null).map(target => targetKey(target)))
  const elsewhere = [...byLine.entries()].filter(([key]) => !shownKeys.has(key)).flatMap(([, comments]) => comments)
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse font-mono text-xs leading-5">
        <tbody>
          {shown.map((row, index) => {
            const target = rowTarget(row)
            const key = target ? targetKey(target) : null
            const thread = key ? byLine.get(key) ?? [] : []
            return (
              <Fragment key={index}>
                <tr className={`group ${ROW_STYLES[row.kind]}`} data-kind={row.kind}>
                  <td className="w-12 select-none border-r border-surface-700 px-2 text-right text-gray-500">{row.oldLine ?? ''}</td>
                  <td className="w-12 select-none border-r border-surface-700 px-2 text-right text-gray-500">{row.newLine ?? ''}</td>
                  <td className="relative w-4 select-none pl-2 text-gray-500">
                    {MARKERS[row.kind]}
                    {canComment && target && (
                      <button type="button" onClick={() => setCommenting(key)}
                        aria-label={t('review.comment.add', { line: target.line })}
                        className="absolute -left-2 top-0.5 rounded bg-brand-600 text-white opacity-0 focus:opacity-100 group-hover:opacity-100">
                        <Plus size={14} aria-hidden="true" />
                      </button>
                    )}
                  </td>
                  <td className="whitespace-pre pr-4">{row.text}</td>
                </tr>
                {target && (thread.length > 0 || commenting === key) && (
                  <tr data-kind="comments">
                    <td colSpan={4} className="space-y-2 border-y border-surface-700 bg-surface-900 p-3">
                      {thread.length > 0 && <CommentThread comments={thread} />}
                      {commenting === key && (
                        <CommentForm path={path} side={target.side} line={target.line} onDone={() => setCommenting(null)} />
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
      {rows.length > shown.length && (
        <button type="button" onClick={() => setShowAll(true)} className="btn btn-ghost btn-sm m-2">
          {t('review.showAllLines', { count: rows.length })}
        </button>
      )}
      {elsewhere.length > 0 && (
        <div className="space-y-2 border-t border-surface-700 p-3">
          <p className="text-xs text-gray-400">{t('review.comment.elsewhere')}</p>
          <CommentThread comments={elsewhere} />
        </div>
      )}
    </div>
  )
}

function FileCard({ file }: { file: FileChange }) {
  const { t } = useTranslation()
  const [viewed, setViewed] = useState(false)
  const [open, setOpen] = useState(true)
  const rows = patchRows(file.patch)
  const expanded = open && !viewed
  const kind = file.change ?? 'unknown'
  return (
    <article id={anchor(file.path)} className="card min-w-0 overflow-hidden p-0" aria-label={file.path}>
      <header className="flex flex-wrap items-center gap-2 border-b border-surface-700 px-3 py-2">
        <button type="button" onClick={() => setOpen(!open)} className="btn-icon text-gray-400"
          aria-expanded={expanded} aria-label={expanded ? t('review.collapse') : t('review.expand')}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        <span className={`badge ${CHANGE_BADGES[kind] ?? 'bg-gray-800 text-gray-400'}`}>{t(`review.change.${kind}`)}</span>
        <span className="min-w-0 flex-1 break-all font-mono text-sm text-gray-100">
          {file.old_path ? `${file.old_path} → ${file.path}` : file.path}
        </span>
        <Counts additions={file.additions} deletions={file.deletions} />
        <label className="flex items-center gap-1 text-xs text-gray-400">
          <input type="checkbox" checked={viewed} onChange={event => setViewed(event.target.checked)} />
          {t('review.viewed')}
        </label>
      </header>
      {expanded && (rows.length > 0 ? <FileDiffTable path={file.path} rows={rows} /> : <FileNotice file={file} />)}
    </article>
  )
}

function Source({ changes }: { changes: RunChanges }) {
  const { t } = useTranslation()
  if (changes.source === 'workspace') {
    return (
      <div className="text-right text-xs text-gray-500">
        <p>{t('review.sourceWorkspace', { id: changes.observed_by?.verification_id ?? '' })}</p>
        {changes.git && (
          <p className="font-mono" data-testid="git-branch">
            {changes.git.commit
              ? t('review.gitCommit', { branch: changes.git.branch, commit: changes.git.commit.slice(0, 7), parent: changes.git.parent.slice(0, 7) })
              : t('review.gitNoCommit', { branch: changes.git.branch })}
          </p>
        )}
      </div>
    )
  }
  if (changes.source === 'artifact') {
    return <p className="text-xs text-amber-300">{t('review.sourceArtifact')}</p>
  }
  return <p className="text-xs text-gray-500">{t('review.sourcePullRequest')}</p>
}

export function ReviewChanges({ companyId, runId, canComment = false }: { companyId: number; runId: number; canComment?: boolean }) {
  const { t } = useTranslation()
  const query = useRunChanges(companyId, runId)
  if (query.isLoading) return <div role="status" className="skeleton h-40 rounded-lg" aria-label={t('review.loading')} />
  if (query.isError || !query.data) {
    return (
      <div role="alert" className="alert-error flex items-center gap-3">
        <span>{t('review.loadError')}</span>
        <button type="button" onClick={() => query.refetch()} className="btn btn-ghost btn-sm">{t('common.retry')}</button>
      </div>
    )
  }
  const changes = query.data
  const declaredOnly = changes.declared_not_observed.length > 0 && (
    <p role="note" className="rounded-lg border border-amber-700/50 bg-amber-950/40 p-3 text-sm text-amber-200">
      {t('review.declaredNotObserved', { files: changes.declared_not_observed.join(', ') })}
    </p>
  )
  if (changes.source === null) {
    return (
      <div className="space-y-3">
        <div className="empty-state py-10">
          <FileDiff className="mx-auto mb-2 text-gray-500" size={28} />
          <p className="text-gray-400">{t('review.nothingObserved')}</p>
          {changes.reason && <p className="mt-1 text-xs text-gray-500">{changes.reason}</p>}
        </div>
        {declaredOnly}
      </div>
    )
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm text-gray-200">
          {t('review.summary', { count: changes.totals.files })}{' '}
          <Counts additions={changes.totals.additions} deletions={changes.totals.deletions} />
        </p>
        <Source changes={changes} />
      </div>
      {changes.git && (
        <PublishBranch companyId={companyId} runId={runId} branch={changes.git.branch} canPublish={canComment} />
      )}
      {declaredOnly}
      {changes.files.length > 1 && (
        <nav aria-label={t('review.fileList')} className="card p-3">
          <ul className="space-y-1 text-sm">
            {changes.files.map(file => (
              <li key={`${file.old_path ?? ''}${file.path}`} className="flex items-center gap-2">
                <a href={`#${anchor(file.path)}`} className="min-w-0 flex-1 break-all font-mono text-brand-300 hover:underline">{file.path}</a>
                <Counts additions={file.additions} deletions={file.deletions} />
              </li>
            ))}
          </ul>
        </nav>
      )}
      <LineReviewProvider companyId={companyId} runId={runId} canComment={canComment}>
        {changes.files.map(file => <FileCard key={`${file.old_path ?? ''}${file.path}`} file={file} />)}
      </LineReviewProvider>
      {changes.files_not_shown > 0 && (
        <p className="text-sm text-gray-400">{t('review.filesNotShown', { count: changes.files_not_shown })}</p>
      )}
    </div>
  )
}
