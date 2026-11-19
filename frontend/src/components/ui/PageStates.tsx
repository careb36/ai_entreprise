/**
 * The states a page shows around its data (docs/contexts/frontend.md, "Design
 * system"): a spinner, a quiet loading line or panel-shaped placeholders while
 * a query loads, an alert with the reason and a Retry tool when it failed, and
 * the notice for a page the person's role does not reach (an `EmptyState`).
 * Every animation stops when the person asked for reduced motion.
 */
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Lock, RefreshCw } from 'lucide-react'
import { EmptyState } from './EmptyState'

/** A decorative spinner: 14px beside text, 13px inside a button (keep the button's words). */
export function Spinner({ size = 14 }: { size?: number }) {
  return <Loader2 size={size} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
}

/** A spinner and its words, announced as a status, while something loads. */
export function LoadingLine({ children }: { children: ReactNode }) {
  return (
    <div role="status" className="flex items-center gap-2 py-2 text-[13px] text-gray-400">
      <Spinner /> {children}
    </div>
  )
}

/** Panel-shaped placeholders while a page's data loads; `label` names them (default "Loading"). */
export function LoadingRows({ rows = 2, label }: { rows?: number; label?: string }) {
  const { t } = useTranslation()
  return (
    <div role="status" aria-label={label ?? t('common.loading')} className="space-y-2">
      {Array.from({ length: rows }, (_, index) => <div key={index} className="skeleton h-14 rounded-xl" />)}
    </div>
  )
}

/** A load that failed: the reason, as an alert, and Retry when `onRetry` is given. */
export function LoadError({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  const { t } = useTranslation()
  return (
    <div role="alert" className="alert-error flex-wrap items-center gap-2">
      <span className="min-w-0 flex-1 break-words">{children}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-tool">
          <RefreshCw size={13} aria-hidden="true" />{t('common.retry')}
        </button>
      )}
    </div>
  )
}

/** Why a page shows nothing to this person: their role does not reach it. */
export function AccessNotice({ children }: { children: ReactNode }) {
  return <EmptyState icon={Lock} title={children} dashed role="status" />
}
