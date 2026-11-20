/**
 * The New company form's remote: the forge policy the Owner chooses, and
 * whether the local copy is brought up to date before the company reads the
 * folder (issue #745).
 *
 * What the daemon observed in the folder is shown first: the remote URL
 * (without credentials), the current and base branch, and whether the
 * checkout is clean, read locally. "Check remote" fetches the base's one
 * branch and says how far it is behind or ahead; nothing contacts the remote
 * before the Owner presses it. The policy defaults to `fetch` only when the
 * folder has a remote, never above the deployment's `FORGE_POLICY_MAX`; the
 * choice is audited at creation. "Bring my local copy up to date" needs
 * `fetch` or above, and only fast-forwards: the daemon refuses, changing
 * nothing, a diverged, dirty or mid-rebase repository, and the company is
 * created either way.
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, RefreshCw } from 'lucide-react'
import { errorDetail, type ForgePolicy, type WorkspaceInspection } from '../services/api'
import { useCheckRemote, useFolderGitCheck, type FolderGitCheck } from '../services/baseSync'
import { useCapabilities } from '../hooks/useQueries'

export const FORGE_POLICIES: ForgePolicy[] = ['none', 'fetch', 'publish']

/** What the Owner chose on the form. */
export interface RemoteChoice {
  forge_policy: ForgePolicy
  sync_base: boolean
}

function capped(policy: ForgePolicy, ceiling: ForgePolicy | undefined): ForgePolicy {
  const limit = FORGE_POLICIES.indexOf(ceiling ?? 'publish')
  return FORGE_POLICIES.indexOf(policy) <= limit ? policy : FORGE_POLICIES[limit]
}

/**
 * What the form sends: the Owner's choice, else `fetch` for a repository with
 * a remote and `none` otherwise, capped by the ceiling. Nothing for a folder
 * that is not a repository.
 */
export function remoteChoiceFor(
  inspection: WorkspaceInspection | null | undefined,
  choice: RemoteChoice | null,
  ceiling: ForgePolicy | undefined,
): Partial<RemoteChoice> {
  if (!inspection?.git?.is_repo) return {}
  const fallback: ForgePolicy = inspection.git.remote_url ? 'fetch' : 'none'
  const policy = capped(choice?.forge_policy ?? fallback, ceiling)
  return { forge_policy: policy, sync_base: policy !== 'none' && Boolean(choice?.sync_base) }
}

/** The Owner's choice, kept for the folder it was made for. */
export function useFolderRemoteChoice(folder: string | null | undefined): [RemoteChoice | null, (choice: RemoteChoice) => void] {
  const [state, setState] = useState<{ folder: string; choice: RemoteChoice } | null>(null)
  const key = folder ?? ''
  return [state && state.folder === key ? state.choice : null, choice => setState({ folder: key, choice })]
}

/** What was read in the folder: remote, branches, checkout. */
function Observed({ observed, remote }: { observed: FolderGitCheck | undefined; remote: string | null }) {
  const { t } = useTranslation()
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
      <dt className="text-gray-400">{t('remoteSync.remote')}</dt>
      <dd className="break-all font-mono text-gray-200">{observed?.remote_url ?? remote ?? t('remoteSync.noRemote')}</dd>
      {observed?.state === 'observed' && (
        <>
          <dt className="text-gray-400">{t('remoteSync.branches')}</dt>
          <dd className="text-gray-200">
            {t('remoteSync.branchesValue', { current: observed.current_branch ?? t('remoteSync.detached'), base: observed.base_ref ?? '' })}
          </dd>
          <dt className="text-gray-400">{t('remoteSync.checkout')}</dt>
          <dd className={observed.clean && !observed.operation_in_progress ? 'text-emerald-300' : 'text-amber-300'}>
            {observed.operation_in_progress
              ? t('remoteSync.inProgress', { operation: observed.operation_in_progress })
              : observed.clean ? t('remoteSync.clean') : t('remoteSync.dirty')}
          </dd>
        </>
      )}
    </dl>
  )
}

/** What "Check remote" observed: counts and a code, never the remote's own words. */
function Counts({ checked }: { checked: FolderGitCheck }) {
  const { t } = useTranslation()
  const behind = checked.base_behind_remote ?? 0
  const ahead = checked.base_ahead_of_remote ?? 0
  const values = { behind, ahead, base: checked.base_ref ?? '', remote: checked.base_remote ?? checked.base_not_on_remote ?? '' }
  let key = 'upToDate'
  if (checked.remote_unreachable) key = checked.remote_unreachable === 'remote_needs_interaction' ? 'needsInteraction' : 'unreachable'
  else if (checked.base_not_on_remote) key = 'notOnRemote'
  else if (behind && ahead) key = 'diverged'
  else if (behind) key = 'behind'
  else if (ahead) key = 'ahead'
  const warn = key !== 'upToDate' && key !== 'ahead'
  return <p role={warn ? 'alert' : 'status'} className={`text-xs ${warn ? 'text-amber-300' : 'text-emerald-300'}`}>{t(`remoteSync.checked.${key}`, values)}</p>
}

export function RemoteSyncChoice({ inspection, base, choice, onChange }: {
  inspection: WorkspaceInspection | null | undefined
  base: string | null
  choice: RemoteChoice | null
  onChange: (choice: RemoteChoice) => void
}) {
  const { t } = useTranslation()
  const repo = Boolean(inspection?.git?.is_repo)
  const path = inspection?.path ?? null
  const local = useFolderGitCheck(path, base, repo)
  const check = useCheckRemote()
  const { data: capabilities } = useCapabilities()
  if (!repo || !inspection) return null
  const ceiling = capabilities?.forge_policy_max
  const value = remoteChoiceFor(inspection, choice, ceiling) as RemoteChoice
  const remote = inspection.git?.remote_url ?? null
  const checked = check.data && check.variables?.path === path && check.variables?.base === base ? check.data : undefined
  return (
    <fieldset className="mt-3 space-y-2 border-t border-surface-700 pt-3">
      <legend className="text-[13px] font-medium text-gray-100">{t('remoteSync.title')}</legend>
      <Observed observed={checked ?? local.data} remote={remote} />
      {local.isError && <p role="alert" className="text-xs text-amber-300">{errorDetail(local.error)}</p>}
      {remote && (
        <div className="space-y-1">
          <button type="button" className="btn-tool h-7 border border-surface-700 px-2" disabled={check.isPending}
            onClick={() => check.mutate({ path: inspection.path, base })}>
            {check.isPending
              ? <Loader2 size={12} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
              : <RefreshCw size={12} aria-hidden="true" />}
            {t('remoteSync.check')}
          </button>
          <p className="text-xs text-gray-400">{t('remoteSync.checkHint', { remote })}</p>
          {check.isError && <p role="alert" className="text-xs text-red-300">{errorDetail(check.error)}</p>}
          {checked && (checked.remote_contacted
            ? <Counts checked={checked} />
            : <p role="note" className="text-xs text-amber-300">{t('remoteSync.notContacted', { ceiling: checked.forge_policy_max ?? '' })}</p>)}
        </div>
      )}
      <div role="radiogroup" aria-label={t('remoteSync.policy')} className="space-y-1">
        <p className="text-xs text-gray-400">{t('remoteSync.policy')}</p>
        {FORGE_POLICIES.map(policy => (
          <label key={policy} className="flex items-start gap-2 text-[13px] text-gray-200">
            <input type="radio" name="new-company-forge-policy" className="mt-0.5 h-4 w-4 flex-shrink-0 accent-brand-500"
              value={policy} checked={value.forge_policy === policy} disabled={capped(policy, ceiling) !== policy}
              onChange={() => onChange({ ...value, forge_policy: policy })} />
            <span>
              <span className="font-medium">{t(`remoteSync.policies.${policy}`)}</span>
              <span className="block text-xs text-gray-400">{t(`forgePolicy.${policy}.hint`)}</span>
            </span>
          </label>
        ))}
      </div>
      <label className="flex items-start gap-2 text-[13px] text-gray-200">
        <input type="checkbox" className="mt-0.5 h-4 w-4 flex-shrink-0 rounded accent-brand-500"
          checked={value.sync_base} disabled={value.forge_policy === 'none' || !remote}
          onChange={event => onChange({ ...value, sync_base: event.target.checked })} />
        <span>
          {t('remoteSync.sync')}
          <span className="block text-xs text-gray-400">{t('remoteSync.syncHint')}</span>
        </span>
      </label>
    </fieldset>
  )
}
