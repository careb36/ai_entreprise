import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Ban, CheckCircle2, Copy, Loader2, ShieldCheck, XCircle } from 'lucide-react'
import type { OwnerActionRun } from '../../services/api'

/** The ids a run recorded, as the platform observed them, in the order an issue cites them. */
export function recordedIds(run: OwnerActionRun): [string, string][] {
  const o = run.observations
  const str = (v: unknown) => (v === null || v === undefined ? '' : String(v))
  const rows: [string, string | number | null | undefined][] = [
    ['run', run.id],
    ['company', o.company_id],
    ['sprint', o.sprint_id],
    ['specification', o.issue_specification_id],
    ['architecture', o.issue_architecture_id],
    ['agentRuns', o.agent_run_ids && o.agent_run_ids.length ? o.agent_run_ids.join(', ') : null],
    ['checkoutHead', o.checkout?.head],
    ['sourceHead', o.checkout?.source_head],
    ['sandboxObserved', o.sandbox?.observed],
    ['containerMode', o.sandbox ? String(o.sandbox.container_mode) : null],
    ['images', o.sandbox?.images?.length ? o.sandbox.images.join(', ') : null],
    ['tokens', o.tokens_observed],
    ['exitStatus', run.exit_status],
    ['mainBefore', str(o.main_before ?? '') || null],
    ['mainAfter', str(o.main_after ?? '') || null],
    ['checks', (o.check_summary as { status?: string } | null | undefined)?.status],
    ['pushedFrom', (o.pushed_range as { from?: string } | undefined)?.from],
    ['pushedTo', (o.pushed_range as { to?: string } | undefined)?.to],
    ['issueUrl', str(o.issue_url ?? '') || null],
    ['commentUrl', str(o.comment_url ?? '') || null],
    ['closed', typeof o.closed === 'boolean' ? String(o.closed) : null],
  ]
  return rows.filter((r): r is [string, string | number] => r[1] !== null && r[1] !== undefined)
    .map(([k, v]) => [k, String(v)])
}

function StatusLine({ run }: { run: OwnerActionRun }) {
  const { t } = useTranslation()
  const icon = run.status === 'running' ? <Loader2 size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
    : run.status === 'succeeded' ? <CheckCircle2 size={13} aria-hidden="true" />
      : run.status === 'cancelled' ? <Ban size={13} aria-hidden="true" /> : <XCircle size={13} aria-hidden="true" />
  return (
    <p className="flex items-center gap-1.5 text-sm font-semibold text-gray-200">
      {icon}
      {t(`ownerActions.status.${run.status}`)}
      {run.cancel_requested_at && run.status === 'running' && ` · ${t('ownerActions.run.stopping')}`}
      {run.reason && ` · ${run.reason}`}
    </p>
  )
}

/** A run: its live status, the steps done, and the ids it recorded, with a copy button. */
export function RunPanel({ run, canCancel, cancelling, onCancel }: {
  run: OwnerActionRun
  canCancel: boolean
  cancelling: boolean
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  const ids = recordedIds(run)
  const text = ids.map(([k, v]) => `${t(`ownerActions.ids.${k}`)}: ${v}`).join('\n')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }
  const steps = run.observations.steps_done ?? []
  const sandbox = run.observations.sandbox

  return (
    <article className="card space-y-3 p-4" aria-label={t('ownerActions.run.label', { id: run.id })}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StatusLine run={run} />
        {run.status === 'running' && canCancel && (
          <button type="button" className="btn-ghost btn-sm" onClick={onCancel} disabled={cancelling || run.cancel_requested_at !== null}>
            <Ban size={13} aria-hidden="true" /> {t('ownerActions.run.cancel')}
          </button>
        )}
      </div>
      <p className="text-xs text-gray-400">
        {t('ownerActions.run.started', { at: run.started_at ?? '' })}
        {run.finished_at && ` · ${t('ownerActions.run.finished', { at: run.finished_at })}`}
      </p>
      {steps.length > 0 && <p className="text-xs text-gray-400">{t('ownerActions.run.steps', { steps: steps.join(' → ') })}</p>}
      {sandbox && (
        <p className="text-xs text-gray-300">
          {t('ownerActions.run.sandbox', { asked: sandbox.asked ?? '—', observed: sandbox.observed })}
          {sandbox.note && ` ${sandbox.note}`}
        </p>
      )}
      {ids.length > 0 && (
        <section className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-gray-400">
              <ShieldCheck size={12} aria-hidden="true" /> {t('ownerActions.run.observed')}
            </h3>
            <button type="button" className="btn-ghost btn-sm" onClick={copy}>
              <Copy size={13} aria-hidden="true" /> {copied ? t('ownerActions.run.copied') : t('ownerActions.run.copy')}
            </button>
          </div>
          <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-xs text-gray-200">
            {ids.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-gray-400">{t(`ownerActions.ids.${k}`)}</dt>
                <dd className="break-all font-mono">{v}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </article>
  )
}
