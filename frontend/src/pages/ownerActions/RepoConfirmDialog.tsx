import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Loader2, Play, RefreshCw } from 'lucide-react'
import type { OwnerActionCommit, OwnerActionPreview } from '../../services/api'

const short = (sha: unknown) => String(sha ?? '').slice(0, 12)

function Commits({ commits, truncated }: { commits: OwnerActionCommit[]; truncated: boolean }) {
  const { t } = useTranslation()
  return (
    <section className="space-y-1">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('ownerActions.repo.commits', { count: commits.length })}</h3>
      <ul className="max-h-40 space-y-0.5 overflow-y-auto font-mono text-xs text-gray-200">
        {commits.map(c => <li key={c.sha}>{short(c.sha)} {c.subject}</li>)}
      </ul>
      {truncated && <p className="text-xs text-gray-500">{t('ownerActions.repo.more')}</p>}
    </section>
  )
}

/**
 * The confirmation of `land_branch`, `push_main` or `close_issue`: what the
 * platform observed (commits, protected paths, the fixing commit) and what the
 * run does. Confirming echoes the preview's digest, which covers those
 * observations and, for `close_issue`, the exact comment: an edited comment
 * is previewed again before it can be confirmed. A protected land asks for
 * the password (the step-up); a push says the repository is public.
 */
export function RepoConfirmDialog({ preview, pending, previewing, error, onConfirm, onRepreview, onClose }: {
  preview: OwnerActionPreview
  pending: boolean
  previewing: boolean
  error: string | null
  onConfirm: (stepUpPassword?: string) => void
  onRepreview: (params: Record<string, unknown>) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  useEffect(() => { dialogRef.current?.focus() }, [])
  const [password, setPassword] = useState('')
  const [comment, setComment] = useState(String(preview.params.comment ?? ''))
  const [override, setOverride] = useState(Boolean(preview.params.override_not_on_origin))
  useEffect(() => {
    setComment(String(preview.params.comment ?? ''))
    setOverride(Boolean(preview.params.override_not_on_origin))
  }, [preview])
  const f = (preview.facts ?? {}) as Record<string, unknown>
  const blockers = preview.blockers ?? []
  const isClose = preview.action === 'close_issue'
  const dirty = isClose && (comment !== String(preview.params.comment ?? '') || override !== Boolean(preview.params.override_not_on_origin))
  const needsPassword = Boolean(preview.step_up)
  const busy = pending || previewing
  const name = t(`ownerActions.actions.${preview.action}.title`)
  const protectedPaths = (f.protected as { path: string; pattern: string }[] | undefined) ?? []
  const diffstat = (f.diffstat as { path: string; added: number | null; deleted: number | null }[] | undefined) ?? []
  const issue = f.issue as { number: number; title: string; url: string | null } | undefined

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-4"
      onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose() }}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
        onKeyDown={event => { if (event.key === 'Escape' && !busy) onClose() }}
        className="flex max-h-[90vh] w-full max-w-lg flex-col gap-4 overflow-y-auto rounded-t-xl border border-surface-700 bg-surface-900 p-5 shadow-xl focus:outline-none sm:rounded-xl">
        <h2 id={titleId} className="text-sm font-semibold text-white">{t('ownerActions.confirm.title', { name })}</h2>
        {preview.action === 'push_main' && (
          <p role="alert" className="flex items-start gap-1.5 rounded border border-amber-500/40 p-2 text-sm font-semibold text-amber-300">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />{t('ownerActions.repo.publicWarning')}
          </p>
        )}
        <section aria-labelledby={`${titleId}-effect`} className="space-y-1">
          <h3 id={`${titleId}-effect`} className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('ownerActions.confirm.effect')}</h3>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-gray-200">
            {preview.effect.map(step => (
              <li key={step.key}>{t(`ownerActions.actions.${preview.action}.effect.${step.key}`, step.values)}</li>
            ))}
          </ol>
        </section>
        {Array.isArray(f.commits) && preview.action !== 'close_issue' && (
          <Commits commits={f.commits as OwnerActionCommit[]} truncated={Boolean(f.commits_truncated)} />
        )}
        {diffstat.length > 0 && (
          <section className="space-y-1">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('ownerActions.repo.diffstat')}</h3>
            <ul className="max-h-32 space-y-0.5 overflow-y-auto font-mono text-xs text-gray-300">
              {diffstat.map(d => <li key={d.path}>{t('ownerActions.repo.diffLine', { path: d.path, added: d.added ?? '-', deleted: d.deleted ?? '-' })}</li>)}
            </ul>
          </section>
        )}
        {protectedPaths.length > 0 && (
          <section className="space-y-1">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-amber-300">{t('ownerActions.repo.protected')}</h3>
            <ul className="space-y-0.5 font-mono text-xs text-amber-200">
              {protectedPaths.map(m => <li key={m.path}>{m.path} ({m.pattern})</li>)}
            </ul>
          </section>
        )}
        {isClose && issue && (
          <section className="space-y-2 text-sm text-gray-200">
            <p>
              {t('ownerActions.repo.issue')}: {issue.url ? <a href={issue.url} target="_blank" rel="noreferrer" className="underline">#{issue.number} {issue.title}</a> : `#${issue.number} ${issue.title}`}
            </p>
            <p>
              {f.fix_commit
                ? `${t('ownerActions.repo.fix')}: ${short(f.fix_commit)} ${String(f.fix_subject ?? '')} · ${t(f.fix_on_origin ? 'ownerActions.repo.onOrigin' : 'ownerActions.repo.notOnOrigin')}`
                : t('ownerActions.repo.noFix')}
            </p>
            <label className="block space-y-1 text-xs text-gray-300">
              <span>{t('ownerActions.repo.comment')}</span>
              <textarea className="input min-h-24 w-full font-mono text-xs" value={comment} maxLength={5000}
                onChange={e => setComment(e.target.value)} />
            </label>
            {!f.fix_on_origin && (
              <label className="flex items-center gap-2 text-xs text-gray-300">
                <input type="checkbox" checked={override} onChange={e => setOverride(e.target.checked)} />
                <span>{t('ownerActions.repo.override')}</span>
              </label>
            )}
            {dirty && (
              <div className="space-y-1">
                <p className="text-xs text-amber-300">{t('ownerActions.repo.repreviewHint')}</p>
                <button type="button" className="btn-ghost btn-sm" disabled={busy || comment.trim() === ''}
                  onClick={() => onRepreview({ ...preview.params, comment, override_not_on_origin: override })}>
                  <RefreshCw size={13} aria-hidden="true" /> {t('ownerActions.repo.repreview')}
                </button>
              </div>
            )}
          </section>
        )}
        {needsPassword && (
          <div className="space-y-1 text-xs text-gray-300">
            <label htmlFor={`${titleId}-password`} className="block">{t('ownerActions.repo.stepUp')}</label>
            <input id={`${titleId}-password`} type="password" className="input w-full" autoComplete="current-password"
              value={password} onChange={e => setPassword(e.target.value)} aria-describedby={`${titleId}-password-hint`} />
            <p id={`${titleId}-password-hint`} className="text-gray-500">{t('ownerActions.repo.stepUpHint')}</p>
          </div>
        )}
        {blockers.length > 0 && (
          <div role="status" className="space-y-1 text-xs text-amber-300">
            <p className="font-semibold">{t('ownerActions.repo.blockedBy')}</p>
            <ul className="list-disc pl-5">{blockers.map(b => <li key={b}>{t(`ownerActions.blockers.${b}`)}</li>)}</ul>
          </div>
        )}
        <p className="text-xs text-gray-400">{t('ownerActions.confirm.audited')}</p>
        {error && <p role="alert" className="alert-error text-xs">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost btn-sm" onClick={onClose} disabled={busy}>{t('common.cancel')}</button>
          <button type="button" className="btn-primary btn-sm" disabled={busy || dirty || blockers.length > 0}
            onClick={() => onConfirm(needsPassword && password ? password : undefined)}>
            {pending ? <Loader2 size={13} className="animate-spin" aria-hidden="true" /> : <Play size={13} aria-hidden="true" />}
            {t('ownerActions.repo.confirm')}
          </button>
        </div>
      </div>
    </div>
  )
}
