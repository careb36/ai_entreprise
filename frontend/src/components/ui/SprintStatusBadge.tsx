import { useTranslation } from 'react-i18next'
import { CircleCheck, CirclePause, CirclePlay, CircleX, Split } from 'lucide-react'
import { StatusBadge, statusLook, type StatusLook } from './StatusBadge'

/** Each sprint status as an icon and a tone; the word comes from `dashboard.status.*`. */
export const SPRINT_STATUS_LOOK: Record<string, StatusLook> = {
  active: { icon: CirclePlay, tone: 'success' },
  needs_split: { icon: Split, tone: 'warning' },
  paused: { icon: CirclePause, tone: 'warning' },
  completed: { icon: CircleCheck, tone: 'neutral' },
  cancelled: { icon: CircleX, tone: 'danger' },
}

/**
 * A sprint's status (the dashboard, the sprint page's header and Overview).
 * An unknown status shows `defaultLabel`, or its own word, with the neutral
 * dashed icon.
 */
export function SprintStatusBadge({ status, defaultLabel }: { status: string; defaultLabel?: string }) {
  const { t } = useTranslation()
  const look = statusLook(SPRINT_STATUS_LOOK, status)
  return (
    <StatusBadge icon={look.icon} tone={look.tone}
      label={t(`dashboard.status.${status}`, { defaultValue: defaultLabel ?? status })} />
  )
}
