import { useTranslation } from 'react-i18next'
import { CircleCheck, CircleDot, CirclePlay, CircleSlash, Hourglass } from 'lucide-react'
import { StatusBadge, statusLook, type StatusLook } from './StatusBadge'

/** Each support ticket status as an icon and a tone; the word comes from `support.status.*`. */
export const TICKET_STATUS_LOOK: Record<string, StatusLook> = {
  open: { icon: CircleDot, tone: 'info' },
  in_progress: { icon: CirclePlay, tone: 'brand' },
  waiting: { icon: Hourglass, tone: 'warning' },
  resolved: { icon: CircleCheck, tone: 'success' },
  closed: { icon: CircleSlash, tone: 'neutral' },
}

/**
 * A support ticket's status (the help desk's list and detail, the support
 * bot's tickets). An unknown status shows its own word with the neutral
 * dashed icon.
 */
export function TicketStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const look = statusLook(TICKET_STATUS_LOOK, status)
  return <StatusBadge icon={look.icon} tone={look.tone} label={t(`support.status.${status}`, { defaultValue: status })} />
}
