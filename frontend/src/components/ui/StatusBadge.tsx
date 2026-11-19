import { CircleDashed, type LucideIcon } from 'lucide-react'

export type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'brand'

/** How one value of a status vocabulary looks: its icon and its tone (the word is the caller's). */
export type StatusLook = { icon: LucideIcon; tone: StatusTone }

const UNKNOWN_LOOK: StatusLook = { icon: CircleDashed, tone: 'neutral' }

/** The look of `status` in `looks`, or the neutral dashed icon for a value the map does not know. */
export function statusLook(looks: Record<string, StatusLook>, status: string): StatusLook {
  return looks[status] ?? UNKNOWN_LOOK
}

const TONES: Record<StatusTone, string> = {
  neutral: 'border-surface-700 bg-surface-800 text-gray-300',
  info: 'border-blue-700/50 bg-blue-950/50 text-blue-300',
  success: 'border-emerald-700/50 bg-emerald-950/50 text-emerald-300',
  warning: 'border-amber-700/50 bg-amber-950/50 text-amber-300',
  danger: 'border-red-700/50 bg-red-950/50 text-red-300',
  brand: 'border-brand-700/50 bg-brand-950/50 text-brand-300',
}

/**
 * A status as an icon and a word, never as colour alone: the tone only
 * repeats what the icon and the label already say. Named apart from the
 * shell's service-health button (`components/shell/StatusPill.tsx`).
 */
export function StatusBadge({ icon: Icon, label, tone = 'neutral' }: {
  icon: LucideIcon
  label: string
  tone?: StatusTone
}) {
  return (
    <span className={`status-badge ${TONES[tone]}`}>
      <Icon size={11} aria-hidden="true" className="flex-shrink-0" />
      {label}
    </span>
  )
}
