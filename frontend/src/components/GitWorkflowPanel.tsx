/**
 * The company's Git workflow, where people look for it.
 *
 * - `GitWorkflowPanel` (the company's Models page, beside the console producers
 *   that need it): the workflow on or off and the base branch task branches
 *   start from, with what the workspace daemon observed just now: whether the
 *   folder is a repository, and how far its local base is behind its remote.
 *   A Manager changes it; a Viewer reads it.
 * - `GitSetupChoice` (New company wizard): the same choice before the company
 *   exists, pre-set from the folder's inspection. The backend keeps it on only
 *   for a folder it observes to be a repository.
 */
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { GitBranch, Loader2 } from 'lucide-react'
import { errorDetail, type WorkspaceInspection } from '../services/api'
import { type GitBaseStatus, type GitWorkflowChoice, useGitWorkflow, useSetGitWorkflow } from '../services/workControl'

/** What the wizard sends: the person's choice, else what the inspection observed. */
export function gitChoiceFor(inspection: WorkspaceInspection | null | undefined, choice: GitWorkflowChoice | null): GitWorkflowChoice {
  const isRepo = Boolean(inspection?.git?.is_repo)
  if (choice) return { git_workflow: isRepo && choice.git_workflow, git_base_branch: choice.git_base_branch?.trim() || null }
  return { git_workflow: isRepo, git_base_branch: inspection?.git?.branch ?? null }
}

/**
 * The wizard's Git choice, kept for the folder it was made for: another folder
 * (typed or picked) starts again from its own inspection.
 */
export function useFolderGitChoice(folder: string | null | undefined): [GitWorkflowChoice | null, (choice: GitWorkflowChoice) => void] {
  const [state, setState] = useState<{ folder: string; choice: GitWorkflowChoice } | null>(null)
  const key = folder ?? ''
  return [state && state.folder === key ? state.choice : null, choice => setState({ folder: key, choice })]
}

/** What the daemon observed in the folder: not a repository, behind its remote, diverged, offline. */
export function BaseStatusNotes({ status }: { status: GitBaseStatus | undefined }) {
  const { t } = useTranslation()
  if (!status) return null
  if (status.state !== 'observed') {
    return <p role="note" className="text-xs text-gray-400">{t('gitSettings.statusUnknown', { reason: status.reason ?? '' })}</p>
  }
  if (!status.is_repo) {
    return <p role="alert" className="text-sm text-amber-300">{t('gitSettings.notRepo', { path: status.path ?? '' })}</p>
  }
  const behind = status.base_behind_remote ?? 0
  const ahead = status.base_ahead_of_remote ?? 0
  return (
    <div className="space-y-1 text-xs">
      <p className="text-gray-400">
        {t('gitSettings.isRepo', { path: status.path ?? '', base: status.base_ref ?? '', commit: (status.base_commit ?? '').slice(0, 12) })}
      </p>
      {behind > 0 && ahead > 0 && (
        <p role="alert" className="text-sm text-red-300">
          {t('gitSettings.diverged', { base: status.base_ref ?? '', remote: status.base_remote ?? '', ahead, behind })}
        </p>
      )}
      {behind > 0 && ahead === 0 && (
        <p role="alert" className="text-sm text-amber-300">
          {t('gitSettings.behind', { count: behind, base: status.base_ref ?? '', remote: status.base_remote ?? '' })}
        </p>
      )}
      {!status.base_remote && <p className="text-gray-400">{t('gitSettings.localOnly')}</p>}
      {status.remote_unreachable && (
        <p role="note" className="text-amber-300">{t('gitSettings.remoteUnreachable', { remote: status.base_remote ?? '', reason: status.remote_unreachable })}</p>
      )}
    </div>
  )
}

function ChoiceFields({ value, disabled, repo, onChange }: {
  value: GitWorkflowChoice
  disabled: boolean
  repo: boolean
  onChange: (value: GitWorkflowChoice) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="space-y-2">
      <label className="flex items-start gap-2 text-sm text-gray-300">
        <input type="checkbox" className="mt-0.5" checked={value.git_workflow} disabled={disabled || !repo}
          onChange={event => onChange({ ...value, git_workflow: event.target.checked })} />
        <span>
          {t('workControl.git')}
          <span className="block text-xs text-gray-500">{t('workControl.gitHint')}</span>
        </span>
      </label>
      <label className="block text-xs text-gray-400">
        {t('gitSettings.baseBranch')}
        <input type="text" className="input mt-1 w-full" value={value.git_base_branch ?? ''} disabled={disabled || !value.git_workflow}
          placeholder={t('gitSettings.basePlaceholder')}
          onChange={event => onChange({ ...value, git_base_branch: event.target.value })} />
      </label>
    </div>
  )
}

/** The Git workflow on the company's settings: the choice, and the folder as observed now. */
export default function GitWorkflowPanel({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const git = useGitWorkflow(companyId, true)
  const save = useSetGitWorkflow(companyId)
  // The draft names the company it was read for, so another company's settings are never saved here.
  const [edit, setEdit] = useState<{ companyId: number; choice: GitWorkflowChoice } | null>(null)
  const [saved, setSaved] = useState(false)
  const setDraft = (choice: GitWorkflowChoice) => setEdit({ companyId, choice })

  useEffect(() => {
    const data = git.data
    if (data && data.company_id === companyId) setEdit({ companyId, choice: { git_workflow: data.git_workflow, git_base_branch: data.git_base_branch } })
  }, [git.data, companyId])

  const draft = edit?.companyId === companyId ? edit.choice : null
  if (git.isLoading || git.isError || !draft) {
    return git.isError ? (
      <div role="alert" className="alert-error text-sm">{errorDetail(git.error)}</div>
    ) : (
      <div role="status" className="flex items-center gap-2 text-sm text-gray-400">
        <Loader2 size={15} className="animate-spin" /> {t('common.loading')}
      </div>
    )
  }
  const status = git.data?.base_status
  // Turning it on is offered unless the daemon observed a folder that is not a repository.
  const repo = !(status?.state === 'observed' && status.is_repo === false) || draft.git_workflow
  return (
    <section aria-labelledby="git-workflow" className="card space-y-4">
      <div className="flex items-center gap-2">
        <GitBranch size={18} className="text-brand-400" />
        <h2 id="git-workflow" className="text-lg font-semibold">{t('gitSettings.title')}</h2>
      </div>
      <BaseStatusNotes status={status} />
      <ChoiceFields value={draft} disabled={!canManage || save.isPending} repo={repo}
        onChange={value => { setSaved(false); setDraft(value) }} />
      {canManage && (
        <div className="flex items-center gap-3">
          <button type="button" className="btn-primary btn-sm" disabled={save.isPending}
            onClick={() => save.mutate({ git_workflow: draft.git_workflow, git_base_branch: draft.git_base_branch?.trim() || null }, { onSuccess: () => setSaved(true) })}>
            {save.isPending
              ? <><Loader2 size={13} className="animate-spin" aria-hidden="true" /><span className="sr-only">{t('gitSettings.save')}</span></>
              : t('gitSettings.save')}
          </button>
          {saved && <span role="status" className="text-xs text-emerald-300">{t('gitSettings.saved')}</span>}
          {save.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(save.error)}</span>}
        </div>
      )}
    </section>
  )
}

/** The wizard's Git choice, pre-set from the folder's inspection. */
export function GitSetupChoice({ inspection, choice, onChange }: {
  inspection: WorkspaceInspection | null | undefined
  choice: GitWorkflowChoice | null
  onChange: (value: GitWorkflowChoice) => void
}) {
  const { t } = useTranslation()
  const repo = Boolean(inspection?.git?.is_repo)
  const value = choice ?? gitChoiceFor(inspection, null)
  return (
    <div className="mt-3 space-y-2">
      {!repo && <p role="note" className="text-xs text-amber-300">{inspection ? t('gitSettings.wizardNotRepo') : t('gitSettings.wizardUnobserved')}</p>}
      <ChoiceFields value={{ ...value, git_workflow: repo && value.git_workflow }} disabled={false} repo={repo} onChange={onChange} />
    </div>
  )
}
