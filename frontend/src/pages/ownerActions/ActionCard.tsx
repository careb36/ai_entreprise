import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ClipboardCheck } from 'lucide-react'
import { useOwnerActionMutations, useOwnerActionRun } from '../../hooks/useOwnerActions'
import { errorDetail, type OwnerAction, type OwnerActionPreview } from '../../services/api'
import { ConfirmDialog } from './ConfirmDialog'
import { RunPanel } from './RunPanel'

const DEFAULT_CHECKOUT = 'la-fabrique-self'

interface Form { checkout_name: string; scope: string; timeout_minutes: number; token_budget: number; urgent: boolean }

function initialForm(action: OwnerAction): Form {
  const p = action.params
  return {
    checkout_name: DEFAULT_CHECKOUT,
    scope: String(p.scope?.default ?? 'S'),
    timeout_minutes: Math.round(Number(p.timeout_seconds?.default ?? 3600) / 60),
    token_budget: Number(p.token_budget?.default ?? 2000000),
    urgent: false,
  }
}

/** One catalogue action: its parameters, the confirmation, its latest run and the earlier ones. */
export function ActionCard({ companyId, action }: { companyId: number; action: OwnerAction }) {
  const { t } = useTranslation()
  const formId = useId()
  const [form, setForm] = useState<Form>(() => initialForm(action))
  const [preview, setPreview] = useState<OwnerActionPreview | null>(null)
  const mutations = useOwnerActionMutations(companyId)
  const latestId = action.running_run_id ?? action.last_runs[0]?.id ?? null
  const latest = useOwnerActionRun(companyId, action.name, latestId)
  const params = {
    checkout_name: form.checkout_name.trim(),
    scope: form.scope,
    timeout_seconds: form.timeout_minutes * 60,
    token_budget: form.token_budget,
    urgent: form.urgent,
  }
  const review = () => mutations.preview.mutate({ name: action.name, params }, { onSuccess: setPreview })
  const confirm = () => {
    if (!preview) return
    mutations.start.mutate(
      { name: action.name, params: preview.params, confirmation: preview.confirmation },
      { onSuccess: () => setPreview(null) },
    )
  }
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm(f => ({ ...f, [key]: value }))
  const running = action.running_run_id !== null
  const run = latest.data ?? action.last_runs[0]

  return (
    <section className="card space-y-4 p-4" aria-labelledby={`${formId}-title`}>
      <header className="space-y-1">
        <h2 id={`${formId}-title`} className="flex items-center gap-2 text-sm font-semibold text-white">
          <ClipboardCheck size={14} aria-hidden="true" />
          {t(`ownerActions.actions.${action.name}.title`)}
          <span className="text-xs font-normal text-gray-500">{t('ownerActions.issue', { issue: action.issue })}</span>
        </h2>
        <p className="text-xs text-gray-400">{t(`ownerActions.actions.${action.name}.description`)}</p>
        {!action.available && (
          <p role="status" className="text-xs text-amber-300">{t(`ownerActions.unavailable.${action.reason ?? 'unknown'}`)}</p>
        )}
      </header>
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={event => { event.preventDefault(); review() }}>
        <div className="space-y-1 text-xs text-gray-300">
          <label htmlFor={`${formId}-checkout`} className="block">{t('ownerActions.form.checkoutName')}</label>
          <input id={`${formId}-checkout`} className="input w-full" value={form.checkout_name}
            onChange={e => set('checkout_name', e.target.value)} aria-describedby={`${formId}-checkout-hint`}
            pattern={action.params.checkout_name?.pattern} required />
          <p id={`${formId}-checkout-hint`} className="text-gray-500">{t('ownerActions.form.checkoutHint')}</p>
        </div>
        <label className="space-y-1 text-xs text-gray-300">
          <span>{t('ownerActions.form.scope')}</span>
          <select className="input w-full" value={form.scope} onChange={e => set('scope', e.target.value)}>
            {(action.params.scope?.enum ?? ['S', 'M']).map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-xs text-gray-300">
          <span>{t('ownerActions.form.timeoutMinutes')}</span>
          <input type="number" className="input w-full" min={1} max={360} value={form.timeout_minutes}
            onChange={e => set('timeout_minutes', Number(e.target.value))} />
        </label>
        <label className="space-y-1 text-xs text-gray-300">
          <span>{t('ownerActions.form.tokenBudget')}</span>
          <input type="number" className="input w-full" min={action.params.token_budget?.minimum} max={action.params.token_budget?.maximum}
            step={10000} value={form.token_budget} onChange={e => set('token_budget', Number(e.target.value))} />
        </label>
        <label className="flex items-center gap-2 text-xs text-gray-300 sm:col-span-2">
          <input type="checkbox" checked={form.urgent} onChange={e => set('urgent', e.target.checked)} />
          <span>{t('ownerActions.form.urgent')}</span>
        </label>
        {mutations.preview.isError && <p role="alert" className="alert-error text-xs sm:col-span-2">{errorDetail(mutations.preview.error)}</p>}
        <div className="sm:col-span-2">
          <button type="submit" className="btn-primary btn-sm" disabled={!action.available || running || mutations.preview.isPending}>
            {running ? t('ownerActions.form.oneAtATime') : t('ownerActions.form.review')}
          </button>
        </div>
      </form>
      {run && (
        <RunPanel run={run} canCancel cancelling={mutations.cancel.isPending}
          onCancel={() => mutations.cancel.mutate({ name: action.name, runId: run.id })} />
      )}
      {action.last_runs.length > 1 && (
        <details className="text-xs text-gray-400">
          <summary>{t('ownerActions.earlierRuns', { count: action.last_runs.length - 1 })}</summary>
          <ul className="mt-2 space-y-1">
            {action.last_runs.slice(1).map(r => (
              <li key={r.id}>{t('ownerActions.earlierRun', { id: r.id, status: t(`ownerActions.status.${r.status}`), at: r.started_at ?? '' })}</li>
            ))}
          </ul>
        </details>
      )}
      {preview && (
        <ConfirmDialog preview={preview} pending={mutations.start.isPending}
          error={mutations.start.isError ? errorDetail(mutations.start.error) : null}
          onConfirm={confirm} onClose={() => { setPreview(null); mutations.start.reset() }} />
      )}
    </section>
  )
}
