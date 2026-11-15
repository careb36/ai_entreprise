import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSodPolicy, useSodPolicyChange } from '../hooks/useDuties'
import { errorDetail, type SodMode, type SodPolicy } from '../services/api'

const MODES: SodMode[] = ['off', 'high_risk', 'all']

/**
 * Who asked for a change does not approve it (issue #543): the company's
 * mode, the access review's schedule, and the single-person exception, which
 * is explicit and audited and only offered while one person can approve.
 */
export default function SodPolicyPanel({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const policy = useSodPolicy(companyId, true)
  const change = useSodPolicyChange(companyId)

  if (policy.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(policy.error)}</div>
  if (!policy.data) return null
  return (
    <section className="card p-4 space-y-4" aria-label={t('duties.policy.title')}>
      <h2 className="text-base font-semibold text-gray-100">{t('duties.policy.title')}</h2>
      {change.isError && <div role="alert" className="alert-error text-sm">{errorDetail(change.error)}</div>}
      <PolicyForm key={policy.data.updated_at ?? 'new'} policy={policy.data} change={change} />
      <SoloException policy={policy.data} change={change} />
    </section>
  )
}

type Change = ReturnType<typeof useSodPolicyChange>

function PolicyForm({ policy, change }: { policy: SodPolicy; change: Change }) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<SodMode>(policy.mode)
  const [reviewInterval, setReviewInterval] = useState(policy.review_interval_days)
  const [inactive, setInactive] = useState(policy.inactive_days)

  return (
    <form
      className="space-y-3"
      onSubmit={e => {
        e.preventDefault()
        change.mutate({ kind: 'update', data: { mode, review_interval_days: reviewInterval, inactive_days: inactive } })
      }}
    >
      <label className="block">
        <span className="input-label">{t('duties.policy.mode')}</span>
        <select className="select w-full" value={mode} onChange={e => setMode(e.target.value as SodMode)}>
          {MODES.map(m => <option key={m} value={m}>{t(`duties.policy.modes.${m}`)}</option>)}
        </select>
      </label>
      <p className="text-xs text-gray-400">{t(`duties.policy.modeHint.${mode}`)}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="input-label">{t('duties.policy.interval')}</span>
          <input type="number" className="input w-full" min={7} max={366} value={reviewInterval}
            onChange={e => setReviewInterval(Number(e.target.value))} />
        </label>
        <label className="block">
          <span className="input-label">{t('duties.policy.inactive')}</span>
          <input type="number" className="input w-full" min={7} max={730} value={inactive}
            onChange={e => setInactive(Number(e.target.value))} />
        </label>
      </div>
      <button type="submit" className="btn-primary btn-sm" disabled={change.isPending}>{t('duties.policy.save')}</button>
    </form>
  )
}

function SoloException({ policy, change }: { policy: SodPolicy; change: Change }) {
  const { t } = useTranslation()
  const [reason, setReason] = useState('')

  if (policy.solo_exception) {
    return (
      <div className="space-y-2 rounded-lg border border-amber-500/40 p-3 text-sm">
        <p className="font-medium text-amber-300">
          {policy.solo_in_force ? t('duties.solo.inForce') : t('duties.solo.notInForce', { count: policy.approvers })}
        </p>
        <p className="text-gray-300">{t('duties.solo.reason', { reason: policy.solo_reason ?? '', by: policy.solo_granted_by ?? '—' })}</p>
        <button type="button" className="btn-secondary btn-sm" disabled={change.isPending}
          onClick={() => change.mutate({ kind: 'withdraw' })}>{t('duties.solo.withdraw')}</button>
      </div>
    )
  }
  if (!policy.solo_eligible) {
    return <p className="text-xs text-gray-400">{t('duties.solo.unavailable', { count: policy.approvers })}</p>
  }
  return (
    <form
      className="space-y-2"
      aria-label={t('duties.solo.title')}
      onSubmit={e => {
        e.preventDefault()
        if (reason.trim().length >= 10) change.mutate({ kind: 'grant', reason: reason.trim() }, { onSuccess: () => setReason('') })
      }}
    >
      <h3 className="text-sm font-semibold text-gray-200">{t('duties.solo.title')}</h3>
      <p className="text-xs text-gray-400">{t('duties.solo.hint')}</p>
      <label className="block">
        <span className="input-label">{t('duties.solo.reasonLabel')}</span>
        <textarea className="input w-full" rows={2} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} />
      </label>
      <button type="submit" className="btn-secondary btn-sm" disabled={reason.trim().length < 10 || change.isPending}>
        {t('duties.solo.grant')}
      </button>
    </form>
  )
}
