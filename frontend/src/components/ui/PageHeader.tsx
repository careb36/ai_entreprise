import type { ReactNode } from 'react'
import { Breadcrumbs, type Crumb } from '../Breadcrumbs'

/**
 * The top of a page: an optional breadcrumb trail, the page's one `<h1>`, a
 * subtitle and the page's actions on the right (they wrap below on a phone).
 */
export function PageHeader({ title, subtitle, actions, breadcrumbs }: {
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  breadcrumbs?: Crumb[]
}) {
  return (
    <header className="page-header">
      <div className="min-w-0 space-y-1.5">
        {breadcrumbs && breadcrumbs.length > 0 && <Breadcrumbs items={breadcrumbs} />}
        <h1 className="page-title">{title}</h1>
        {subtitle && <p className="page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}
