import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { CircleCheck, CircleDashed, CircleMinus, CircleX, Hand, ShieldCheck, type LucideIcon } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import { WSTG_STATUSES, useWstgPlan, useWstgReviews, type WstgEvidence, type WstgRow, type WstgStatus } from '../services/securityTests'
import { useAppStore } from '../store/useAppStore'
import {
  CountPill, EmptyState, LoadError, LoadingRows, PageHeader, Panel, SegmentedFilter, StatusBadge, type StatusTone, TABLE, TD, TH, TR,
} from '../components/ui'
import { WstgReviewForm } from './securityTests/WstgReviewForm'

export const WSTG_LOOK: Record<WstgStatus, { icon: LucideIcon; tone: StatusTone }> = {
  'observed-pass': { icon: CircleCheck, tone: 'success' },
  'observed-fail': { icon: CircleX, tone: 'danger' },
  'needs-manual': { icon: Hand, tone: 'warning' },
  'not-run': { icon: CircleDashed, tone: 'neutral' },
  'not-applicable': { icon: CircleMinus, tone: 'neutral' },
}

type Filter = WstgStatus | 'all'

/**
 * Security tests (`/company/security-tests`, issue #539): the project's OWASP
 * WSTG plan with each test's status and what set it (a Verifier's
 * observation, linked to its run, or a person's review), the recorded
 * reviews, and for a Manager a form to record one. Every member reads it.
 */
export default function SecurityTests() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canRecord = role === 'Owner' || role === 'Manager'
  const plan = useWstgPlan(companyId)
  const reviews = useWstgReviews(companyId)
  const [filter, setFilter] = useState<Filter>('all')
  const rows = plan.data?.rows ?? []
  const shown = filter === 'all' ? rows : rows.filter(row => row.status === filter)

  return (
    <div className="page-container max-w-6xl space-y-4">
      <PageHeader title={t('securityTests.title')} subtitle={t('securityTests.subtitle')} />
      {plan.isLoading ? (
        <LoadingRows rows={3} label={t('securityTests.loading')} />
      ) : plan.isError ? (
        <LoadError onRetry={() => plan.refetch()}>{t('securityTests.loadError', { message: errorDetail(plan.error) })}</LoadError>
      ) : plan.data ? (
        <>
          <SegmentedFilter
            label={t('securityTests.filterLabel')}
            value={filter}
            onChange={value => setFilter(value as Filter)}
            options={[
              { value: 'all', label: t('securityTests.all'), count: rows.length },
              ...WSTG_STATUSES.map(status => ({
                value: status, label: t(`securityTests.status.${status}`), icon: WSTG_LOOK[status].icon, count: plan.data.counts[status] ?? 0,
                attention: status === 'observed-fail' || status === 'needs-manual',
              })),
            ]}
          />
          <Panel title={t('securityTests.planTitle')} count={<CountPill count={shown.length} total={rows.length} label={t('securityTests.count', { count: shown.length })} />}>
            {shown.length === 0 ? (
              <EmptyState icon={ShieldCheck} title={t('securityTests.empty')} />
            ) : (
              <div className="overflow-x-auto">
                <table className={TABLE}>
                  <caption className="sr-only">{t('securityTests.planTitle')}</caption>
                  <thead>
                    <tr>
                      <th scope="col" className={TH}>{t('securityTests.columns.test')}</th>
                      <th scope="col" className={TH}>{t('securityTests.columns.mode')}</th>
                      <th scope="col" className={TH}>{t('securityTests.columns.status')}</th>
                      <th scope="col" className={TH}>{t('securityTests.columns.setBy')}</th>
                    </tr>
                  </thead>
                  <tbody>{shown.map(row => <PlanRow key={row.id} row={row} />)}</tbody>
                </table>
              </div>
            )}
          </Panel>
          {canRecord && <WstgReviewForm rows={rows} />}
          <ReviewsPanel reviews={reviews} />
          <p className="text-meta">{t('securityTests.attribution', { commit: plan.data.catalogue.commit, licence: plan.data.catalogue.licence })} {plan.data.catalogue.attribution}</p>
        </>
      ) : null}
    </div>
  )
}

function PlanRow({ row }: { row: WstgRow }) {
  const { t } = useTranslation()
  const look = WSTG_LOOK[row.status] ?? WSTG_LOOK['not-run']
  return (
    <tr className={`${TR} align-top`}>
      <td className={TD}>
        <div className="text-[13px] text-gray-100">{row.name}</div>
        <div className="text-meta"><code className="font-mono">{row.id}</code> · {row.category}</div>
        {row.claim && <div className="mt-0.5 text-[11px] text-gray-400">{t('securityTests.claimed')}</div>}
      </td>
      <td className={`${TD} text-gray-300`}>{row.mode ? t(`securityTests.mode.${row.mode}`) : '—'}</td>
      <td className={TD}><StatusBadge icon={look.icon} tone={look.tone} label={t(`securityTests.status.${row.status}`)} /></td>
      <td className={`${TD} text-gray-300`}>{row.evidence ? <EvidenceCell evidence={row.evidence} /> : <span className="text-gray-400">{row.reason}</span>}</td>
    </tr>
  )
}

function EvidenceCell({ evidence }: { evidence: WstgEvidence }) {
  const { t } = useTranslation()
  if (evidence.kind === 'verification') {
    return (
      <span>
        {t('securityTests.byVerifier')}{' '}
        {evidence.run_id != null && <Link to={`/evidence/${evidence.run_id}`} className="text-brand-300 hover:underline">{t('securityTests.run', { id: evidence.run_id })}</Link>}
        {evidence.observation && <span className="block text-[11px] text-gray-400">{evidence.observation}</span>}
      </span>
    )
  }
  return (
    <span>
      {t('securityTests.byReview', { role: evidence.reviewer_role ?? '—', date: evidence.reviewed_on ?? '—' })}
      {evidence.artifact && <span className="block text-[11px] text-gray-400">{t('securityTests.artifact', { artifact: evidence.artifact })}</span>}
    </span>
  )
}

function ReviewsPanel({ reviews }: { reviews: ReturnType<typeof useWstgReviews> }) {
  const { t } = useTranslation()
  const rows = reviews.data?.reviews ?? []
  return (
    <Panel title={t('securityTests.reviewsTitle')} count={rows.length > 0 ? <CountPill count={rows.length} label={t('securityTests.reviewCount', { count: rows.length })} /> : undefined}>
      {reviews.isLoading ? (
        <div className="p-3"><LoadingRows rows={1} label={t('securityTests.reviewsLoading')} /></div>
      ) : reviews.isError ? (
        <div className="p-3"><LoadError onRetry={() => reviews.refetch()}>{errorDetail(reviews.error)}</LoadError></div>
      ) : rows.length === 0 ? (
        <p className="px-3 py-3 text-xs text-gray-400">{t('securityTests.noReviews')}</p>
      ) : (
        <ul className="data-list" aria-label={t('securityTests.reviewsTitle')}>
          {rows.map(review => (
            <li key={review.id} className="data-row space-y-0.5 px-3 py-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <code className="chip font-mono">{review.wstg_id}</code>
                <span className="text-gray-100">{t(`securityTests.result.${review.result}`)}</span>
                <span className="text-gray-400">{t('securityTests.reviewedBy', { name: review.reviewer_name, role: review.reviewer_role, date: review.reviewed_on })}</span>
              </div>
              <div className="text-gray-400">{t('securityTests.artifact', { artifact: review.artifact })}{review.note ? ` · ${review.note}` : ''}</div>
              <div className="text-meta break-all font-mono" title={t('securityTests.digest')}>{review.record_sha256}</div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}
