/**
 * When the sprint planner's model did not answer usably, the plan is generic:
 * the note says so, with the reason the backend observed, and offers to plan
 * again. Shown on the plan preview (QuickLaunch) and on the sprint's board
 * (the backend's record, `services/planning/planner_fallback.py`).
 *
 * `ObjectiveText` shows the objective the person wrote, whole, on demand: a
 * name or a goal line may be shortened for display, never the stored text.
 */
import { useTranslation } from 'react-i18next'
import { AlertTriangle, RotateCcw } from 'lucide-react'
import type { PlannerFallback } from '../services/api'

const REASONS = ['no_model', 'model_error', 'unparseable', 'too_few_tasks', 'unexpected_error']

export function PlannerFallbackNote({ fallback, onRetry, retryLabel, busy = false, unplannedItems = [] }: {
  fallback: PlannerFallback | null | undefined
  onRetry?: () => void
  retryLabel?: string
  busy?: boolean
  unplannedItems?: string[]
}) {
  const { t } = useTranslation()
  if (!fallback) return null
  const reason = REASONS.includes(fallback.reason) ? fallback.reason : 'unexpected_error'
  return (
    <div role="alert" className="rounded-lg border border-amber-600/40 bg-amber-950/20 p-2.5 text-[11px] text-amber-200 space-y-1.5">
      <p className="flex items-start gap-1.5 font-semibold">
        <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
        {t('plannerFallback.title')}
      </p>
      <p>{t(`plannerFallback.reasons.${reason}`)}</p>
      {fallback.detail && (
        <p className="break-words font-mono text-[10px] text-amber-300/80">
          {t('plannerFallback.observed', { detail: fallback.detail })}
        </p>
      )}
      <p className="text-amber-200/80">{t('plannerFallback.kept')}</p>
      {unplannedItems.length > 0 && (
        <p>{t('plannerFallback.unplanned', { count: unplannedItems.length, items: unplannedItems.join('; ') })}</p>
      )}
      {onRetry && (
        <button type="button" onClick={onRetry} disabled={busy}
          className="inline-flex items-center gap-1 rounded-md border border-amber-500/40 px-2 py-1 font-medium text-amber-100 hover:bg-amber-500/10 disabled:opacity-50 disabled:cursor-not-allowed">
          <RotateCcw size={11} aria-hidden="true" />
          {retryLabel ?? t('plannerFallback.retry')}
        </button>
      )}
    </div>
  )
}

export function ObjectiveText({ objective }: { objective: string | null | undefined }) {
  const { t } = useTranslation()
  if (!objective) return null
  return (
    <details className="text-[11px] text-gray-400">
      <summary className="cursor-pointer select-none text-gray-300">
        {t('plannerFallback.objective', { count: objective.length })}
      </summary>
      <p className="mt-1 max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded border border-surface-700 bg-surface-900/70 p-2">
        {objective}
      </p>
    </details>
  )
}
