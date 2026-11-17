/**
 * The loading and error states the specification and knowledge pages share
 * (Specifications, Architecture, Findings, Documentation, Wiki, Procedures,
 * Memory): a quiet line with a spinner while a query loads, and an alert with
 * the backend's reason and a Retry tool when it failed. Local to these pages;
 * a candidate for `components/ui` once another group needs the same.
 */
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, RefreshCw } from 'lucide-react'

/** The class for a long block of text: a readable line length, relaxed leading. */
export const READABLE_TEXT = 'max-w-prose whitespace-pre-wrap break-words text-sm leading-relaxed text-gray-200'

export function Spinner({ size = 14 }: { size?: number }) {
  return <Loader2 size={size} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
}

export function LoadingLine({ children }: { children: ReactNode }) {
  return (
    <div role="status" className="flex items-center gap-2 py-2 text-[13px] text-gray-400">
      <Spinner /> {children}
    </div>
  )
}

export function LoadErrorAlert({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  const { t } = useTranslation()
  return (
    <div role="alert" className="alert-error flex flex-wrap items-center justify-between gap-2">
      <span className="min-w-0">{children}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-tool">
          <RefreshCw size={13} aria-hidden="true" />{t('common.retry')}
        </button>
      )}
    </div>
  )
}
