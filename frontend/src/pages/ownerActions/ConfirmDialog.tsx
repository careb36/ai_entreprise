import { useEffect, useId, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Loader2, Play } from 'lucide-react'
import type { OwnerActionPreview } from '../../services/api'
import { SodNotice, sodOf } from './SodNotice'

/**
 * The confirmation before an owner action runs (issue #701): what it does
 * and what it costs, as the backend's preview returned them. Confirming
 * sends the preview's digest back, so what this dialog showed is what runs
 * (a start with other parameters is refused). When segregation of duties
 * would refuse the run's approvals, the dialog says so before she confirms,
 * with a link to that company's policy; a blocker disables the start.
 */
export function ConfirmDialog({ preview, pending, error, onConfirm, onClose }: {
  preview: OwnerActionPreview
  pending: boolean
  error: string | null
  onConfirm: () => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const titleId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)
  useEffect(() => { dialogRef.current?.focus() }, [])
  const minutes = Math.ceil(preview.cost.max_seconds / 60)
  const blockers = preview.blockers ?? []
  const sod = sodOf(preview.facts?.sod)

  return (
    <div className="fixed inset-0 z-[60] flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-4"
      onMouseDown={event => { if (event.target === event.currentTarget && !pending) onClose() }}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
        onKeyDown={event => { if (event.key === 'Escape' && !pending) onClose() }}
        className="flex max-h-[90vh] w-full max-w-lg flex-col gap-4 overflow-y-auto rounded-t-xl border border-surface-700 bg-surface-900 p-5 shadow-xl focus:outline-none sm:rounded-xl">
        <h2 id={titleId} className="text-sm font-semibold text-white">
          {t('ownerActions.confirm.title', { name: t(`ownerActions.actions.${preview.action}.title`) })}
        </h2>
        <section aria-labelledby={`${titleId}-effect`} className="space-y-1">
          <h3 id={`${titleId}-effect`} className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('ownerActions.confirm.effect')}</h3>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-gray-200">
            {preview.effect.map(step => (
              <li key={step.key}>{t(`ownerActions.actions.${preview.action}.effect.${step.key}`, step.values)}</li>
            ))}
          </ol>
        </section>
        <section aria-labelledby={`${titleId}-cost`} className="space-y-1">
          <h3 id={`${titleId}-cost`} className="text-xs font-semibold uppercase tracking-wide text-gray-400">{t('ownerActions.confirm.cost')}</h3>
          <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-sm text-gray-200">
            <dt className="text-gray-400">{t('ownerActions.confirm.models')}</dt>
            <dd>{preview.cost.models.join(', ')}</dd>
            <dt className="text-gray-400">{t('ownerActions.confirm.tokenBudget')}</dt>
            <dd>{preview.cost.token_budget.toLocaleString()}</dd>
            <dt className="text-gray-400">{t('ownerActions.confirm.duration')}</dt>
            <dd>{t('ownerActions.confirm.minutes', { count: minutes })}</dd>
          </dl>
          {preview.cost.budget_checked_between_calls && (
            <p className="flex items-start gap-1.5 text-xs text-amber-300">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
              {t('ownerActions.confirm.budgetBetweenCalls')}
            </p>
          )}
        </section>
        {blockers.length > 0 && (
          <div role="status" className="space-y-1 text-xs text-amber-300">
            <p className="font-semibold">{t('ownerActions.repo.blockedBy')}</p>
            <ul className="list-disc pl-5">{blockers.map(b => <li key={b}>{t(`ownerActions.blockers.${b}`)}</li>)}</ul>
          </div>
        )}
        {sod && <SodNotice sod={sod} />}
        <p className="text-xs text-gray-400">{t('ownerActions.confirm.audited')}</p>
        {error && <p role="alert" className="alert-error text-xs">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className="btn-ghost btn-sm" onClick={onClose} disabled={pending}>{t('common.cancel')}</button>
          <button type="button" className="btn-primary btn-sm" onClick={onConfirm} disabled={pending || blockers.length > 0}>
            {pending ? <Loader2 size={13} className="animate-spin" aria-hidden="true" /> : <Play size={13} aria-hidden="true" />}
            {t('ownerActions.confirm.run')}
          </button>
        </div>
      </div>
    </div>
  )
}
