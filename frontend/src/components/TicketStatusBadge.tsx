import { useTranslation } from 'react-i18next'
import { CircleCheck, CircleDashed, CircleDot, CirclePlay, CircleSlash, Hourglass, type LucideIcon } from 'lucide-react'
import { StatusBadge, type StatusTone } from './ui'

/** Each support ticket status as an icon, a word and a tone: never the colour alone. */
export const TICKET_STATUS_LOOK: Record<string, { icon: LucideIcon; tone: StatusTone }> = {
  open: { icon: CircleDot, tone: 'info' },
  in_progress: { icon: CirclePlay, tone: 'brand' },
  waiting: { icon: Hourglass, tone: 'warning' },
  resolved: { icon: CircleCheck, tone: 'success' },
  closed: { icon: CircleSlash, tone: 'neutral' },
}

/**
 * A support ticket's status (the help desk's list and detail, the support
 * bot's tickets), in the design system's `StatusBadge`. An unknown status
 * shows its own word with a neutral dashed icon.
 */
export function TicketStatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const look = TICKET_STATUS_LOOK[status] ?? { icon: CircleDashed, tone: 'neutral' as const }
  return <StatusBadge icon={look.icon} tone={look.tone} label={t(`support.status.${status}`, { defaultValue: status })} />
}
