import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

/**
 * What a list says when it has nothing to show: an icon, a short title, an
 * optional sentence and the actions that would fill it. `dashed` draws the
 * board's dashed outline, for an empty state that stands alone on the page;
 * `role="status"` announces it, for one that replaces the page (`AccessNotice`).
 */
export function EmptyState({ icon: Icon, title, description, actions, dashed = false, role }: {
  icon?: LucideIcon
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  dashed?: boolean
  role?: 'status'
}) {
  return (
    <div role={role} className={`empty-state ${dashed ? 'rounded-xl border border-dashed border-surface-700' : ''}`}>
      {Icon && <Icon size={24} aria-hidden="true" className="empty-state-icon" />}
      <p className="empty-state-title">{title}</p>
      {description && <p className="empty-state-desc">{description}</p>}
      {actions && <div className="mt-1 flex flex-wrap justify-center gap-2">{actions}</div>}
    </div>
  )
}
