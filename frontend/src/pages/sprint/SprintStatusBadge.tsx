/**
 * A sprint's status as the design system's `StatusBadge`: an icon, a word and
 * a tone, never the colour alone. The same look as the Sprints list
 * (`pages/Sprints.tsx`), shared by the sprint page's header and its Overview.
 * A candidate for `components/ui/` once a page outside the sprint needs it.
 */
import { useTranslation } from 'react-i18next'
import { CircleCheck, CircleDashed, CirclePause, CirclePlay, CircleX, Split, type LucideIcon } from 'lucide-react'
import { StatusBadge, type StatusTone } from '../../components/ui'

const STATUS_LOOK: Record<string, { icon: LucideIcon; tone: StatusTone }> = {
  active: { icon: CirclePlay, tone: 'success' },
  needs_split: { icon: Split, tone: 'warning' },
  paused: { icon: CirclePause, tone: 'warning' },
  completed: { icon: CircleCheck, tone: 'neutral' },
  cancelled: { icon: CircleX, tone: 'danger' },
}

const FALLBACK: { icon: LucideIcon; tone: StatusTone } = { icon: CircleDashed, tone: 'neutral' }

export function SprintStatusBadge({ status, defaultLabel }: { status: string; defaultLabel?: string }) {
  const { t } = useTranslation()
  const look = STATUS_LOOK[status] ?? FALLBACK
  return (
    <StatusBadge icon={look.icon} tone={look.tone}
      label={t(`dashboard.status.${status}`, { defaultValue: defaultLabel ?? status })} />
  )
}
