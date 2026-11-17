import { useId, type ReactNode } from 'react'

/**
 * A calm surface for one list, table or group of fields. With a `title` it is
 * a `<section>` named by its header (a landmark a screen reader can jump to),
 * with an optional count (a `CountPill`) and actions on the right; without
 * one it is a plain container.
 */
export function Panel({ title, count, actions, children, className = '', headingLevel = 2 }: {
  title?: ReactNode
  count?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  headingLevel?: 2 | 3
}) {
  const headingId = useId()
  if (!title) return <div className={`panel ${className}`}>{children}</div>
  const Heading = headingLevel === 3 ? 'h3' : 'h2'
  return (
    <section aria-labelledby={headingId} className={`panel ${className}`}>
      <header className="panel-header">
        <Heading id={headingId} className="panel-title">{title}</Heading>
        {count}
        {actions && <div className="ml-auto flex flex-wrap items-center gap-1">{actions}</div>}
      </header>
      {children}
    </section>
  )
}
