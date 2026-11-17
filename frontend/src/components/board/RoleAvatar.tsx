import { Bot, UserRound } from 'lucide-react'
import { roleInitials, roleTone } from '../../lib/kanban'

/**
 * A role's round mark on a card or in the activity panel: its initials on a
 * tone of its own. Decorative: the role's name is always beside it or in the
 * accessible name of what contains it.
 */
export function RoleAvatar({ role, size = 'md' }: { role: string | null | undefined; size?: 'sm' | 'md' }) {
  const box = size === 'sm' ? 'h-5 w-5 text-[9px]' : 'h-6 w-6 text-[10px]'
  if (!role) {
    return (
      <span aria-hidden="true" className={`${box} inline-flex flex-shrink-0 items-center justify-center rounded-full bg-surface-700 text-gray-400`}>
        <UserRound size={size === 'sm' ? 10 : 12} />
      </span>
    )
  }
  if (role.toLowerCase() === 'system') {
    return (
      <span aria-hidden="true" className={`${box} inline-flex flex-shrink-0 items-center justify-center rounded-full bg-surface-700 text-gray-300 ring-1 ring-surface-600`}>
        <Bot size={size === 'sm' ? 10 : 12} />
      </span>
    )
  }
  return (
    <span aria-hidden="true"
      className={`${box} inline-flex flex-shrink-0 select-none items-center justify-center rounded-full font-semibold tracking-tight ring-1 ${roleTone(role)}`}>
      {roleInitials(role)}
    </span>
  )
}
