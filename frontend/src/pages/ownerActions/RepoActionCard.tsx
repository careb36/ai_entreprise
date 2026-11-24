import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { GitBranch } from 'lucide-react'
import { useOwnerActionMutations, useOwnerActionOptions, useOwnerActionRun } from '../../hooks/useOwnerActions'
import { errorDetail, type OwnerAction, type OwnerActionPreview } from '../../services/api'
import { RepoConfirmDialog } from './RepoConfirmDialog'
import { RunPanel } from './RunPanel'

/**
 * `land_branch`, `push_main` or `close_issue` (issue #701): the Owner picks a
 * branch the daemon lists or an open issue (never types one), reviews what the
 * platform observed, and confirms in the dialog. One run at a time.
 */
export function RepoActionCard({ companyId, action }: { companyId: number; action: OwnerAction }) {
  const { t } = useTranslation()
  const formId = useId()
  const [branch, setBranch] = useState('')
  const [issue, setIssue] = useState('')
  const [preview, setPreview] = useState<OwnerActionPreview | null>(null)
  const mutations = useOwnerActionMutations(companyId)
  const picks = action.name !== 'push_main'
  const options = useOwnerActionOptions(companyId, action.name, picks && action.available)
  const latestId = action.running_run_id ?? action.last_runs[0]?.id ?? null
  const latest = useOwnerActionRun(companyId, action.name, latestId)
  const running = action.running_run_id !== null
  const run = latest.data ?? action.last_runs[0]
  const params: Record<string, unknown> = action.name === 'land_branch' ? { branch }
    : action.name === 'close_issue' ? { issue_number: Number(issue) } : {}
  const ready = action.name === 'land_branch' ? branch !== '' : action.name === 'close_issue' ? issue !== '' : true
  const review = (p: Record<string, unknown>) => mutations.preview.mutate({ name: action.name, params: p }, { onSuccess: setPreview })
  const confirm = (stepUpPassword?: string) => {
    if (!preview) return
    mutations.start.mutate(
      { name: action.name, params: preview.params, confirmation: preview.confirmation, stepUpPassword },
      { onSuccess: () => setPreview(null) },
    )
  }
  const branches = options.data?.branches ?? []
  const issues = options.data?.issues ?? []

  return (
    <section className="card space-y-4 p-4" aria-labelledby={`${formId}-title`}>
      <header className="space-y-1">
        <h2 id={`${formId}-title`} className="flex items-center gap-2 text-sm font-semibold text-white">
          <GitBranch size={14} aria-hidden="true" />
          {t(`ownerActions.actions.${action.name}.title`)}
          <span className="text-xs font-normal text-gray-500">{t('ownerActions.issue', { issue: action.issue })}</span>
        </h2>
        <p className="text-xs text-gray-400">{t(`ownerActions.actions.${action.name}.description`)}</p>
        {!action.available && (
          <p role="status" className="text-xs text-amber-300">{t(`ownerActions.unavailable.${action.reason ?? 'unknown'}`)}</p>
        )}
      </header>
      <form className="space-y-3" onSubmit={event => { event.preventDefault(); review(params) }}>
        {picks && options.isPending && action.available && <p className="text-xs text-gray-400">{t('ownerActions.form.loadingOptions')}</p>}
        {options.isError && <p role="alert" className="alert-error text-xs">{t('ownerActions.form.optionsError', { message: errorDetail(options.error) })}</p>}
        {action.name === 'land_branch' && options.isSuccess && (
          branches.length === 0 ? <p className="text-xs text-gray-400">{t('ownerActions.form.noBranches')}</p> : (
            <label className="block space-y-1 text-xs text-gray-300">
              <span>{t('ownerActions.form.branch')}</span>
              <select className="input w-full" value={branch} onChange={e => setBranch(e.target.value)}>
                <option value="">{t('ownerActions.form.branchPlaceholder')}</option>
                {branches.map(b => <option key={b.name} value={b.name}>{b.name} · {b.subject}</option>)}
              </select>
            </label>
          )
        )}
        {action.name === 'close_issue' && options.isSuccess && (
          issues.length === 0 ? <p className="text-xs text-gray-400">{t('ownerActions.form.noIssues')}</p> : (
            <label className="block space-y-1 text-xs text-gray-300">
              <span>{t('ownerActions.form.issue')}</span>
              <select className="input w-full" value={issue} onChange={e => setIssue(e.target.value)}>
                <option value="">{t('ownerActions.form.issuePlaceholder')}</option>
                {issues.map(i => <option key={i.number} value={String(i.number)}>#{i.number} {i.title}</option>)}
              </select>
            </label>
          )
        )}
        {action.name === 'push_main' && <p className="text-xs text-gray-400">{t('ownerActions.form.pushHint')}</p>}
        {mutations.preview.isError && !preview && <p role="alert" className="alert-error text-xs">{errorDetail(mutations.preview.error)}</p>}
        <button type="submit" className="btn-primary btn-sm" disabled={!action.available || running || !ready || mutations.preview.isPending}>
          {running ? t('ownerActions.form.oneAtATime') : t('ownerActions.form.review')}
        </button>
      </form>
      {run && (
        <RunPanel run={run} canCancel cancelling={mutations.cancel.isPending}
          onCancel={() => mutations.cancel.mutate({ name: action.name, runId: run.id })} />
      )}
      {preview && (
        <RepoConfirmDialog preview={preview} pending={mutations.start.isPending} previewing={mutations.preview.isPending}
          error={mutations.start.isError ? errorDetail(mutations.start.error) : mutations.preview.isError ? errorDetail(mutations.preview.error) : null}
          onConfirm={confirm} onRepreview={review}
          onClose={() => { setPreview(null); mutations.start.reset(); mutations.preview.reset() }} />
      )}
    </section>
  )
}
