import { useTranslation } from 'react-i18next'
import { CircleAlert, CircleCheck, CirclePause, CirclePlay, CircleX, GitMerge, Split } from 'lucide-react'
import { StatusBadge, statusLook, type StatusLook } from './StatusBadge'
import type { IntegrationState } from '../../services/api/sprints'
import { shownSprintStatus } from '../../lib/sprintStatus'

/** Each sprint status as an icon and a tone; the word comes from `dashboard.status.*`. */
export const SPRINT_STATUS_LOOK: Record<string, StatusLook> = {
  active: { icon: CirclePlay, tone: 'success' },
  needs_split: { icon: Split, tone: 'warning' },
  paused: { icon: CirclePause, tone: 'warning' },
  completed: { icon: CircleCheck, tone: 'neutral' },
  cancelled: { icon: CircleX, tone: 'danger' },
  integrating: { icon: GitMerge, tone: 'warning' },
  integration_failed: { icon: CircleAlert, tone: 'danger' },
}

/**
 * A sprint's status (the dashboard, the sprint page's header and Overview).
 * An unknown status shows `defaultLabel`, or its own word, with the neutral
 * dashed icon. A completed sprint whose integration is still running or
 * failed shows that instead (`lib/sprintStatus.ts`).
 */
export function SprintStatusBadge({ status: stored, integrationState, defaultLabel }: {
  status: string; integrationState?: IntegrationState | null; defaultLabel?: string
}) {
  const { t } = useTranslation()
  const status = shownSprintStatus(stored, integrationState)
  const look = statusLook(SPRINT_STATUS_LOOK, status)
  return (
    <StatusBadge icon={look.icon} tone={look.tone}
      label={t(`dashboard.status.${status}`, { defaultValue: defaultLabel ?? status })} />
  )
}
