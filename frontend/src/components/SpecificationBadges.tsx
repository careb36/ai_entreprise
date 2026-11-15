/**
 * A specification's status badge and the summary of its deterministic check,
 * shown on the Specifications page, a specification's and an architecture's
 * detail, and the architecture summary.
 */
import { useTranslation } from 'react-i18next'
import type { Specification } from '../services/specifications'

const STATUS_TONE: Record<Specification['status'], string> = {
  draft: 'bg-amber-900/50 text-amber-300',
  approved: 'bg-emerald-900/50 text-emerald-300',
  superseded: 'bg-gray-800 text-gray-400',
}

export function StatusBadge({ status }: { status: Specification['status'] }) {
  const { t } = useTranslation()
  return <span className={`badge ${STATUS_TONE[status]}`}>{t(`specifications.status.${status}`, { defaultValue: status })}</span>
}

/** "N blocking · M warnings", or that nothing blocks it. */
export function CheckSummary({ check }: { check: Specification['check'] }) {
  const { t } = useTranslation()
  if (check.ok && check.warnings === 0) return <span className="text-xs text-emerald-300">{t('specifications.ready')}</span>
  return (
    <span className={`text-xs ${check.blocking > 0 ? 'text-red-300' : 'text-amber-300'}`}>
      {t('specifications.checkSummary', { blocking: check.blocking, warnings: check.warnings })}
    </span>
  )
}
