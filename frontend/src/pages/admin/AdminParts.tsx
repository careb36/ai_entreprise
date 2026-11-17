/**
 * Small pieces the company and administration pages share (Members, Teams,
 * SSO, Duties, Chat approvals, GitHub, Audit anchors, Models). They are built
 * from the design system (components/ui, docs/contexts/frontend.md, "Design
 * system") and kept here until a second group of pages needs them; each is a
 * candidate for the foundation.
 */
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { CircleCheck, Loader2, Lock, RefreshCw } from 'lucide-react'
import { EmptyState } from '../../components/ui'

/** Why a page shows nothing to this person: their role does not reach it. */
export function AccessNotice({ children }: { children: ReactNode }) {
  return (
    <div role="status">
      <EmptyState icon={Lock} title={children} dashed />
    </div>
  )
}

/** A panel-shaped placeholder while a page's data loads. */
export function LoadingRows({ rows = 2, label }: { rows?: number; label?: string }) {
  const { t } = useTranslation()
  return (
    <div role="status" aria-label={label ?? t('common.loading')} className="space-y-2">
      {Array.from({ length: rows }, (_, index) => <div key={index} className="skeleton h-14 rounded-xl" />)}
    </div>
  )
}

/** A spinner inside a button, with the motion off when the person asked for less. */
export function ButtonSpinner() {
  return <Loader2 size={13} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
}

/** A load error with the way to try again. */
export function LoadError({ message, onRetry }: { message: ReactNode; onRetry?: () => void }) {
  const { t } = useTranslation()
  return (
    <div role="alert" className="alert-error items-center text-sm">
      <span className="min-w-0 flex-1 break-words">{message}</span>
      {onRetry && (
        <button type="button" className="btn-tool" onClick={onRetry}>
          <RefreshCw size={13} aria-hidden="true" />{t('common.retry')}
        </button>
      )}
    </div>
  )
}

/** A short confirmation after a save: an icon and a word, never the colour alone. */
export function SavedNote({ children }: { children: ReactNode }) {
  return (
    <span role="status" className="inline-flex items-center gap-1 text-xs text-emerald-300">
      <CircleCheck size={13} aria-hidden="true" />{children}
    </span>
  )
}

/** The row of buttons at the foot of a settings form. */
export function FormActions({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2 border-t border-surface-700 pt-3">{children}</div>
}
