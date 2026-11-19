import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { useAccessReview, useCompleteAccessReview } from '../hooks/useDuties'
import { CalendarClock, Clock } from 'lucide-react'
import { errorDetail, type AccessDecision, type AccessReviewListing, type AccessReviewSso } from '../services/api'
import { Panel, TABLE, TD, TH, TR } from './ui'

type Key = `${AccessDecision['kind']}:${number}`
const CHOICES: AccessDecision['decision'][] = ['confirm', 'remove']

/**
 * The periodic access review (issue #543): every member with their own,
 * team-derived and effective role, links and last activity, and every working
 * API token, each confirmed or removed by the Owner, and what the SSO group
 * mapping keeps outside the provider's say (issue #703): exclusions, manual
 * exceptions and Manager pairs one Owner confirmed alone. The schedule says
 * when the next one is due, and flags a missed one.
 */
export default function AccessReviewPanel({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const review = useAccessReview(companyId, true)

  if (review.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(review.error)}</div>
  if (!review.data) return null
  return (
    <Panel title={t('duties.review.title')}>
      <div className="p-3"><Schedule schedule={review.data.schedule} /></div>
      <ReviewForm key={review.data.history[0]?.id ?? 'first'} listing={review.data} companyId={companyId} />
      <div className="border-t border-surface-700 px-3 py-2"><History history={review.data.history} /></div>
    </Panel>
  )
}

function Schedule({ schedule }: { schedule: AccessReviewListing['schedule'] }) {
  const { t } = useTranslation()
  const due = schedule.due_at ? new Date(schedule.due_at).toLocaleDateString() : t('duties.review.now')
  const text = t(`duties.review.state.${schedule.state}`, { due, days: schedule.interval_days })
  return schedule.state === 'missed'
    ? <div role="alert" className="alert-error text-sm">{text}</div>
    : (
      <p role="status" className="flex items-center gap-1.5 text-[13px] text-gray-200">
        <CalendarClock size={14} aria-hidden="true" className="flex-shrink-0 text-gray-400" />{text}
      </p>
    )
}

function ReviewForm({ listing, companyId }: { listing: AccessReviewListing; companyId: number | null }) {
  const { t } = useTranslation()
  const complete = useCompleteAccessReview(companyId)
  const [decisions, setDecisions] = useState<Record<Key, AccessDecision['decision']>>({})
  const [note, setNote] = useState('')
  const items = [...listing.members, ...listing.tokens, ...(listing.sso ?? [])]
  const decided = items.every(i => decisions[`${i.kind}:${i.id}`])

  function choose(key: Key, value: AccessDecision['decision']) {
    setDecisions(current => ({ ...current, [key]: value }))
  }

  function submit() {
    const payload = items.map(i => ({ kind: i.kind, id: i.id, decision: decisions[`${i.kind}:${i.id}`] }))
    complete.mutate({ decisions: payload, note: note.trim() || null })
  }

  return (
    <form className="space-y-3" onSubmit={e => { e.preventDefault(); if (decided) submit() }}>
      {complete.isError && <div className="px-3"><div role="alert" className="alert-error text-sm">{errorDetail(complete.error)}</div></div>}
      <div className="overflow-x-auto">
        <table className={TABLE}>
          <thead>
            <tr>
              <th className={TH}>{t('duties.review.account')}</th>
              <th className={TH}>{t('duties.review.access')}</th>
              <th className={TH}>{t('duties.review.lastActivity')}</th>
              <th className={TH}>{t('duties.review.decision')}</th>
            </tr>
          </thead>
          <tbody>
            {items.map(item => {
              const key: Key = `${item.kind}:${item.id}`
              const access = item.kind === 'member'
                ? [
                    t('duties.review.roles', { own: item.own_role, effective: item.effective_role ?? '—' }),
                    ...item.teams.map(team => t('duties.review.team', { name: team.name, role: team.role })),
                    item.sso_linked ? t('duties.review.sso') : null,
                    ...item.chat_links.map(p => t('duties.review.chat', { provider: p })),
                  ].filter(Boolean).join(' · ')
                : item.kind === 'token'
                  ? t('duties.review.token', { role: item.role, by: item.created_by ?? '—' })
                  : ssoAccess(t, item)
              return (
                <tr key={key} className={`${TR} align-top`}>
                  <td className={TD}>
                    <div className="text-[13px] text-gray-100">{item.label}</div>
                    {item.inactive && (
                      <span className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-amber-300">
                        <Clock size={11} aria-hidden="true" />{t('duties.review.inactive')}
                      </span>
                    )}
                  </td>
                  <td className={`${TD} text-gray-300`}>{access}</td>
                  <td className={`${TD} tabular-nums text-gray-400`}>
                    {!('last_activity' in item) ? '—'
                      : item.last_activity ? new Date(item.last_activity).toLocaleDateString() : t('duties.review.never')}
                  </td>
                  <td className={TD}>
                    <fieldset aria-label={t('duties.review.decideFor', { name: item.label })} className="flex gap-3">
                      {CHOICES.map(value => (
                        <label key={value} className="flex items-center gap-1 text-gray-200">
                          <input type="radio" className="accent-brand-500" name={key} checked={decisions[key] === value} onChange={() => choose(key, value)} />
                          {t(`duties.review.${value}`)}
                        </label>
                      ))}
                    </fieldset>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-end gap-2 px-3 pb-3">
        <label className="block min-w-[14rem] flex-1">
          <span className="input-label">{t('duties.review.note')}</span>
          <input className="input w-full" maxLength={1000} value={note} onChange={e => setNote(e.target.value)} />
        </label>
        <button type="submit" className="btn-primary btn-sm" disabled={!decided || complete.isPending}>
          {t('duties.review.complete')}
        </button>
      </div>
    </form>
  )
}

function ssoAccess(t: TFunction, item: AccessReviewSso) {
  const team = item.team_name ?? `#${item.team_id}`
  if (item.kind === 'exclusion') {
    return t('duties.review.ssoExclusion', { team, reason: item.reason ?? '—', by: item.created_by ?? '—', days: item.age_days ?? 0 })
  }
  if (item.kind === 'manual_exception') {
    return t('duties.review.ssoException', { team, by: item.created_by ?? '—', days: item.age_days ?? 0 })
  }
  return t('duties.review.ssoManagerPair', { value: item.group_value ?? item.label, team, by: item.decided_by ?? '—' })
}

function History({ history }: { history: AccessReviewListing['history'] }) {
  const { t } = useTranslation()
  if (history.length === 0) return <p className="text-xs text-gray-400">{t('duties.review.noHistory')}</p>
  return (
    <ul className="space-y-1 text-xs text-gray-400" aria-label={t('duties.review.history')}>
      {history.map(h => (
        <li key={h.id}>
          {t(h.on_time ? 'duties.review.doneOnTime' : 'duties.review.doneLate', {
            when: new Date(h.completed_at).toLocaleDateString(),
            by: h.completed_by,
            removed: h.summary.removed ?? 0,
          })}
        </li>
      ))}
    </ul>
  )
}
