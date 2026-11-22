import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useRoutineRevisions } from '../../hooks/useRoutines'
import { LoadError, LoadingLine } from '../../components/ui'
import { localTime } from '../../lib/utcTime'

/** A routine's revisions, newest first; a Manager restores an earlier one as a new revision. */
export function RoutineRevisions({ routineId, current, readOnly, restoring, onRestore }: {
  routineId: number
  current: number
  readOnly: boolean
  restoring: boolean
  onRestore: (number: number) => void
}) {
  const { t } = useTranslation()
  const revisions = useRoutineRevisions(routineId)
  const [confirming, setConfirming] = useState<number | null>(null)

  if (revisions.isLoading) return <LoadingLine>{t('routines.revisions.loading')}</LoadingLine>
  if (revisions.isError) return <LoadError onRetry={() => revisions.refetch()}>{t('routines.revisions.loadError')}</LoadError>

  return (
    <ol className="space-y-2">
      {(revisions.data?.revisions ?? []).map(rev => (
        <li key={rev.number} className="rounded-lg border border-surface-700 p-2 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-gray-200">
              {t('routines.revisions.number', { number: rev.number })}
              {rev.number === current && <span className="ml-2 text-brand-300">{t('routines.revisions.current')}</span>}
              <span className="ml-2 text-gray-400">
                {rev.change_summary ?? ''}{rev.created_at ? ` · ${localTime(rev.created_at)}` : ''}
              </span>
            </p>
            {!readOnly && rev.number !== current && (
              <button type="button" className="btn-tool border border-surface-700" disabled={restoring}
                onClick={() => {
                  if (confirming !== rev.number) return setConfirming(rev.number)
                  setConfirming(null)
                  onRestore(rev.number)
                }}>
                {confirming === rev.number ? t('routines.revisions.confirmRestore') : t('routines.revisions.restore')}
              </button>
            )}
          </div>
          <details className="mt-1">
            <summary className="cursor-pointer text-gray-400">{t('routines.revisions.show')}</summary>
            <pre className="mt-1 whitespace-pre-wrap break-words text-gray-400">{rev.snapshot.description || '—'}</pre>
          </details>
        </li>
      ))}
    </ol>
  )
}
