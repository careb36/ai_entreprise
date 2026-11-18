import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Building2, FlaskConical } from 'lucide-react'
import { FindingOutcomes, FindingStatusBadge } from '../components/FindingOutcomes'
import { LoadErrorAlert, LoadingLine } from '../components/knowledge/PageStates'
import { CountPill, EmptyState, PageHeader, Panel } from '../components/ui'
import { useFindings } from '../hooks/useFindings'
import { errorDetail, type LoopFinding } from '../services/api'
import { useAppStore } from '../store/useAppStore'

const findingPath = (id: number) => `/company/findings/${id}`

function FindingRow({ finding }: { finding: LoopFinding }) {
  const { t } = useTranslation()
  const headingId = `finding-${finding.id}`
  return (
    <li className="data-row py-3">
      <article aria-labelledby={headingId} className="space-y-2.5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <h3 id={headingId} className="min-w-0 text-[13px] font-semibold text-gray-100">
            <Link to={findingPath(finding.id)} className="rounded-sm hover:text-brand-300 hover:underline">{finding.title}</Link>
          </h3>
          <FindingStatusBadge status={finding.status} />
        </div>
        <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[max-content_1fr]">
          <dt className="text-meta sm:leading-[1.125rem]">{t('findings.source')}</dt>
          <dd className="min-w-0 break-words text-gray-200">
            {finding.source ?? t('findings.noSource')}
            {finding.source_ref && <> · <code className="text-gray-300">{finding.source_ref}</code></>}
          </dd>
          <dt className="text-meta sm:leading-[1.125rem]">{t('findings.targetMeasure')}</dt>
          <dd className="min-w-0 text-gray-200">
            {finding.target_measure
              ? <code className="whitespace-pre-wrap break-words">{finding.target_measure}</code>
              : t('findings.noTargetMeasure')}
          </dd>
        </dl>
        <FindingOutcomes outcomes={finding.outcomes} label={t('findings.outcomes.caption', { title: finding.title })} />
      </article>
    </li>
  )
}

/**
 * The company's self-improvement findings (`docs/design/rsi-loop.md`, §1 and
 * §7), read-only for every member: what the platform observed, the measure
 * that should pass once it is fixed, and what each cycle run for it decided.
 * Nothing on this page starts, retries or closes a cycle.
 */
export default function CompanyFindings() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const query = useFindings(companyId)
  const findings = query.data?.findings ?? []

  return (
    <div className="page-container max-w-5xl space-y-4">
      <PageHeader title={t('findings.title')} subtitle={t('findings.subtitle')} />

      {companyId === null ? (
        <EmptyState icon={Building2} title={t('findings.noCompany')} dashed />
      ) : query.isPending ? (
        <LoadingLine>{t('findings.loading')}</LoadingLine>
      ) : query.isError ? (
        <LoadErrorAlert onRetry={() => query.refetch()}>{t('findings.loadError', { message: errorDetail(query.error) })}</LoadErrorAlert>
      ) : findings.length === 0 ? (
        <EmptyState icon={FlaskConical} title={t('findings.empty')} dashed />
      ) : (
        <Panel title={t('findings.listTitle')}
          count={<CountPill count={findings.length} label={t('findings.countLabel', { count: findings.length })} />}>
          <ul className="data-list">
            {findings.map(finding => <FindingRow key={finding.id} finding={finding} />)}
          </ul>
        </Panel>
      )}
    </div>
  )
}
