import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, RefreshCw } from 'lucide-react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

function ErrorFallback({ error, onReset }: { error: Error | null; onReset: () => void }) {
  const { t } = useTranslation()
  return (
    <div className="p-4 sm:p-6">
      <div role="alert" className="empty-state mx-auto max-w-lg rounded-xl border border-dashed border-surface-700 px-6">
        <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-red-800/50 bg-red-950/40">
          <AlertTriangle size={20} aria-hidden="true" className="text-red-300" />
        </span>
        <p className="empty-state-title">{t('errorBoundary.title')}</p>
        <p className="empty-state-desc break-words">
          {error?.message ?? t('errorBoundary.description')}
        </p>
        <button type="button" onClick={onReset} className="btn-secondary btn-sm mt-1">
          <RefreshCw size={13} aria-hidden="true" />
          {t('errorBoundary.retry')}
        </button>
      </div>
    </div>
  )
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  reset = () => this.setState({ hasError: false, error: null })

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      return <ErrorFallback error={this.state.error} onReset={this.reset} />
    }
    return this.props.children
  }
}
