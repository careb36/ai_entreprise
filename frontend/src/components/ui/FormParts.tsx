import type { ReactNode } from 'react'
import { CircleCheck } from 'lucide-react'

/** The row of buttons at the foot of a settings form. */
export function FormActions({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2 border-t border-surface-700 pt-3">{children}</div>
}

/** A short confirmation after a save, announced as a status: an icon and a word, never the colour alone. */
export function SavedNote({ children }: { children: ReactNode }) {
  return (
    <span role="status" className="inline-flex items-center gap-1 text-xs text-emerald-300">
      <CircleCheck size={13} aria-hidden="true" />{children}
    </span>
  )
}
