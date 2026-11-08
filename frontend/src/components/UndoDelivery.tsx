/**
 * Undoing a task's delivery, for a company without the Git workflow. Before
 * writing, the workspace daemon kept the files it was about to change in a
 * store of its own, outside the project (the idea is opencode's snapshots).
 * A Manager undoes the delivery after confirming: each file the delivery
 * changed gets its bytes back and the ones it created are removed, only
 * while every one still holds what the delivery wrote. The task's status
 * stays as it is; a review sends it back to work if wanted.
 */
import { useTranslation } from 'react-i18next'
import { Loader2, Undo2 } from 'lucide-react'
import { errorDetail } from '../services/api'
import type { DeliverySnapshot } from '../services/reviewChanges'
import { useUndoDelivery } from '../services/runReviews'

export function UndoDelivery({ companyId, runId, snapshot }: {
  companyId: number; runId: number; snapshot: DeliverySnapshot
}) {
  const { t } = useTranslation()
  const undo = useUndoDelivery(companyId, runId)
  const confirmAndUndo = () => {
    if (!window.confirm(t('review.undo.confirm', { count: snapshot.files.length }))) return
    undo.mutate()
  }
  return (
    <section aria-label={t('review.undo.title')} className="card space-y-2 p-3">
      {snapshot.undone_at && !undo.isSuccess ? (
        <p role="note" className="text-xs text-gray-400">
          {t('review.undo.undoneAt', { when: new Date(snapshot.undone_at).toLocaleString() })}
        </p>
      ) : !undo.isSuccess && (
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={confirmAndUndo} disabled={undo.isPending}
            className="btn btn-ghost btn-sm flex items-center gap-1.5">
            {undo.isPending ? <Loader2 size={13} className="animate-spin" /> : <Undo2 size={13} aria-hidden="true" />}
            {t('review.undo.button')}
          </button>
          <p className="min-w-0 flex-1 text-xs text-gray-500">{t('review.undo.hint', { count: snapshot.files.length })}</p>
        </div>
      )}
      {undo.isSuccess && (
        <div role="status" className="space-y-1 text-xs text-gray-300">
          <p>{t('review.undo.done', { restored: undo.data.restored.length, removed: undo.data.removed.length })}</p>
          <ul className="font-mono">
            {undo.data.restored.map(file => <li key={`r-${file.path}`}>{t('review.undo.restored', { path: file.path })}</li>)}
            {undo.data.removed.map(file => <li key={`d-${file.path}`}>{t('review.undo.removed', { path: file.path })}</li>)}
          </ul>
          <p className="text-gray-500">{t('review.undo.statusStays')}</p>
        </div>
      )}
      {undo.isError && <p role="alert" className="text-sm text-red-300">{errorDetail(undo.error)}</p>}
    </section>
  )
}
