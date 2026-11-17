import type { ReactNode } from 'react'

/**
 * The row of filters, presets and actions above a list. `sticky` keeps it at
 * the top of the scrolling page, as the board's toolbar stays above its
 * columns. Its children are ordinary controls in tab order (`btn-tool`,
 * `input control-sm`, a `SegmentedFilter`); it is a group, not an ARIA
 * toolbar, so no arrow-key roving is expected.
 */
export function Toolbar({ label, sticky = false, children, className = '' }: {
  label: string
  sticky?: boolean
  children: ReactNode
  className?: string
}) {
  return (
    <div role="group" aria-label={label} className={`toolbar ${sticky ? 'toolbar-sticky' : ''} ${className}`}>
      {children}
    </div>
  )
}
