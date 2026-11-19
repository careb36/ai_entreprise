/**
 * "Bring the local copy up to date" after the company exists (issue #745).
 *
 * - `BaseSyncButton` (the Git workflow settings and the board's notice): a
 *   Manager asks the workspace daemon to fast-forward the company's local base
 *   to its remote, held to the forge policy; what the daemon observed is shown
 *   and recorded on the company. A refusal changed nothing and says why.
 * - `BaseBehindNotice` (the board of a sprint that has not started): when the
 *   daemon observed the local base behind its remote (only under a policy that
 *   lets it read the remote), says so, with the action beside it. New sprint
 *   branches already start from the remote's base (`git_base`); this is about
 *   the person's own checkout.
 */
import { useTranslation } from 'react-i18next'
import { CircleCheck, Loader2, RefreshCw } from 'lucide-react'
import { errorDetail } from '../services/api'
import { SYNC_DONE, useSyncBase } from '../services/baseSync'
import { useGitWorkflow } from '../services/workControl'

export function BaseSyncButton({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const sync = useSyncBase(companyId)
  const result = sync.data
  return (
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className="btn-tool h-7 border border-surface-700 px-2" disabled={sync.isPending}
        onClick={() => sync.mutate()}>
        {sync.isPending
          ? <Loader2 size={12} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
          : <RefreshCw size={12} aria-hidden="true" />}
        {t('remoteSync.syncAction')}
      </button>
      {result && SYNC_DONE.includes(result.outcome) && (
        <span role="status" className="inline-flex items-center gap-1 text-xs text-emerald-300">
          <CircleCheck size={13} aria-hidden="true" />
          {t(`remoteSync.outcomes.${result.outcome}`, { count: result.behind ?? 0, base: result.base ?? '', remote: result.remote ?? '' })}
        </span>
      )}
      {result && !SYNC_DONE.includes(result.outcome) && (
        <span role="alert" className="text-xs text-amber-300">
          {t('remoteSync.refused', {
            outcome: t(`remoteSync.outcomes.${result.outcome}`, { defaultValue: result.outcome }),
            reason: result.reason ?? '',
          })}
        </span>
      )}
      {sync.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(sync.error)}</span>}
    </div>
  )
}

export function BaseBehindNotice({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const git = useGitWorkflow(companyId, true)
  const status = git.data?.base_status
  const behind = status?.base_behind_remote ?? 0
  if (!status || status.state !== 'observed' || behind === 0 || (status.base_ahead_of_remote ?? 0) > 0) return null
  return (
    <div role="note" className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
      <span className="text-amber-200">
        {t('remoteSync.behindNotice', { count: behind, base: status.base_ref ?? '', remote: status.base_remote ?? '' })}
      </span>
      {canManage && <BaseSyncButton companyId={companyId} />}
    </div>
  )
}
