/**
 * ErrorState — reusable error display component, in the empty state's look
 * (components/ui/EmptyState): an icon and a word, the danger tone repeating them.
 */

import { AlertTriangle, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

interface ErrorStateProps {
  message: string
  onRetry?: () => void
  className?: string
}

export function ErrorState({ message, onRetry, className = '' }: ErrorStateProps) {
  const { t } = useTranslation()
  return (
    <div role="alert" className={`empty-state px-4 ${className}`}>
      <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-red-800/50 bg-red-950/40">
        <AlertTriangle size={20} aria-hidden="true" className="text-red-300" />
      </span>
      <p className="empty-state-title">{t('errorState.title', { defaultValue: 'Something went wrong' })}</p>
      <p className="empty-state-desc break-words">{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="btn-tool mt-1 border border-surface-700">
          <RefreshCw size={12} aria-hidden="true" />
          {t('errorState.retry', { defaultValue: 'Try again' })}
        </button>
      )}
    </div>
  )
}
