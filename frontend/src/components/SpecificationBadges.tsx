/**
 * A specification's status badge and the summary of its deterministic check,
 * shown on the Specifications page, a specification's and an architecture's
 * detail, and the architecture summary. Both are an icon and a word, never a
 * colour alone (docs/contexts/frontend.md, Design system).
 */
import { useTranslation } from 'react-i18next'
import { Archive, CircleAlert, CircleCheck, PencilLine, TriangleAlert, type LucideIcon } from 'lucide-react'
import type { Specification } from '../services/specifications'
import { StatusBadge as Badge, type StatusTone } from './ui'

const STATUS_LOOK: Record<Specification['status'], { icon: LucideIcon; tone: StatusTone }> = {
  draft: { icon: PencilLine, tone: 'warning' },
  approved: { icon: CircleCheck, tone: 'success' },
  superseded: { icon: Archive, tone: 'neutral' },
}

export function StatusBadge({ status }: { status: Specification['status'] }) {
  const { t } = useTranslation()
  const look = STATUS_LOOK[status] ?? STATUS_LOOK.draft
  return <Badge icon={look.icon} tone={look.tone} label={t(`specifications.status.${status}`, { defaultValue: status })} />
}

/** "N blocking · M warnings", or that nothing blocks it. */
export function CheckSummary({ check }: { check: Specification['check'] }) {
  const { t } = useTranslation()
  if (check.ok && check.warnings === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-emerald-300">
        <CircleCheck size={12} aria-hidden="true" />{t('specifications.ready')}
      </span>
    )
  }
  const blocking = check.blocking > 0
  const Icon = blocking ? CircleAlert : TriangleAlert
  return (
    <span className={`inline-flex items-center gap-1 text-xs tabular-nums ${blocking ? 'text-red-300' : 'text-amber-300'}`}>
      <Icon size={12} aria-hidden="true" />{t('specifications.checkSummary', { blocking: check.blocking, warnings: check.warnings })}
    </span>
  )
}
