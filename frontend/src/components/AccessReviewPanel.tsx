import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAccessReview, useCompleteAccessReview } from '../hooks/useDuties'
import { errorDetail, type AccessDecision, type AccessReviewListing } from '../services/api'

type Key = `${'member' | 'token'}:${number}`
const CHOICES: AccessDecision['decision'][] = ['confirm', 'remove']

/**
 * The periodic access review (issue #543): every member with their own,
 * team-derived and effective role, links and last activity, and every working
 * API token, each confirmed or removed by the Owner. The schedule says when
 * the next one is due, and flags a missed one.
 */
export default function AccessReviewPanel({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const review = useAccessReview(companyId, true)

  if (review.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(review.error)}</div>
  if (!review.data) return null
  return (
    <section className="card p-4 space-y-4" aria-label={t('duties.review.title')}>
      <h2 className="text-base font-semibold text-gray-100">{t('duties.review.title')}</h2>
      <Schedule schedule={review.data.schedule} />
      <ReviewForm key={review.data.history[0]?.id ?? 'first'} listing={review.data} companyId={companyId} />
      <History history={review.data.history} />
    </section>
  )
}

function Schedule({ schedule }: { schedule: AccessReviewListing['schedule'] }) {
  const { t } = useTranslation()
  const due = schedule.due_at ? new Date(schedule.due_at).toLocaleDateString() : t('duties.review.now')
  const text = t(`duties.review.state.${schedule.state}`, { due, days: schedule.interval_days })
  return schedule.state === 'missed'
    ? <div role="alert" className="alert-error text-sm">{text}</div>
    : <p role="status" className="text-sm text-gray-300">{text}</p>
}

function ReviewForm({ listing, companyId }: { listing: AccessReviewListing; companyId: number | null }) {
  const { t } = useTranslation()
  const complete = useCompleteAccessReview(companyId)
  const [decisions, setDecisions] = useState<Record<Key, AccessDecision['decision']>>({})
  const [note, setNote] = useState('')
  const items = [...listing.members, ...listing.tokens]
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
      {complete.isError && <div role="alert" className="alert-error text-sm">{errorDetail(complete.error)}</div>}
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-gray-400">
          <tr>
            <th className="py-1">{t('duties.review.account')}</th>
            <th>{t('duties.review.access')}</th>
            <th>{t('duties.review.lastActivity')}</th>
            <th>{t('duties.review.decision')}</th>
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
              : t('duties.review.token', { role: item.role, by: item.created_by ?? '—' })
            return (
              <tr key={key} className="border-t border-surface-700 align-top">
                <td className="py-2">
                  <div className="text-gray-100">{item.label}</div>
                  {item.inactive && <span className="text-xs text-amber-300">{t('duties.review.inactive')}</span>}
                </td>
                <td className="py-2 text-gray-300">{access}</td>
                <td className="py-2 text-gray-400">
                  {item.last_activity ? new Date(item.last_activity).toLocaleDateString() : t('duties.review.never')}
                </td>
                <td className="py-2">
                  <fieldset aria-label={t('duties.review.decideFor', { name: item.label })} className="flex gap-3">
                    {CHOICES.map(value => (
                      <label key={value} className="flex items-center gap-1">
                        <input type="radio" name={key} checked={decisions[key] === value} onChange={() => choose(key, value)} />
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
      <label className="block">
        <span className="input-label">{t('duties.review.note')}</span>
        <input className="input w-full" maxLength={1000} value={note} onChange={e => setNote(e.target.value)} />
      </label>
      <button type="submit" className="btn-primary btn-sm" disabled={!decided || complete.isPending}>
        {t('duties.review.complete')}
      </button>
    </form>
  )
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
