import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { FlaskConical, Loader2 } from 'lucide-react'
import { FindingOutcomes, FindingStatusBadge } from '../components/FindingOutcomes'
import { useFindings } from '../hooks/useFindings'
import { errorDetail, type LoopFinding } from '../services/api'
import { useAppStore } from '../store/useAppStore'

const findingPath = (id: number) => `/company/findings/${id}`

function FindingCard({ finding }: { finding: LoopFinding }) {
  const { t } = useTranslation()
  const headingId = `finding-${finding.id}`
  return (
    <article aria-labelledby={headingId} className="card p-4 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h2 id={headingId} className="text-base font-semibold text-gray-100">
          <Link to={findingPath(finding.id)} className="hover:text-white hover:underline">{finding.title}</Link>
        </h2>
        <FindingStatusBadge status={finding.status} />
      </div>
      <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[max-content_1fr]">
        <dt className="text-gray-400">{t('findings.source')}</dt>
        <dd className="text-gray-200">
          {finding.source ?? t('findings.noSource')}
          {finding.source_ref && <> · <code className="text-gray-300">{finding.source_ref}</code></>}
        </dd>
        <dt className="text-gray-400">{t('findings.targetMeasure')}</dt>
        <dd className="text-gray-200">
          {finding.target_measure
            ? <code className="whitespace-pre-wrap break-words">{finding.target_measure}</code>
            : t('findings.noTargetMeasure')}
        </dd>
      </dl>
      <FindingOutcomes outcomes={finding.outcomes} label={t('findings.outcomes.caption', { title: finding.title })} />
    </article>
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
    <div className="page-container max-w-5xl space-y-6">
      <div className="page-header">
        <div>
          <div className="flex items-center gap-2">
            <FlaskConical className="text-brand-400" size={22} aria-hidden="true" />
            <h1 className="page-title">{t('findings.title')}</h1>
          </div>
          <p className="text-gray-400 mt-1">{t('findings.subtitle')}</p>
        </div>
      </div>

      {companyId === null ? (
        <p role="status" className="card p-4 text-sm text-gray-300">{t('findings.noCompany')}</p>
      ) : query.isPending ? (
        <div role="status" className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={15} className="animate-spin" aria-hidden="true" /> {t('findings.loading')}
        </div>
      ) : query.isError ? (
        <div role="alert" className="alert-error flex flex-wrap items-center gap-2 text-sm">
          <span>{t('findings.loadError', { message: errorDetail(query.error) })}</span>
          <button type="button" onClick={() => query.refetch()} className="underline">{t('common.retry')}</button>
        </div>
      ) : findings.length === 0 ? (
        <p role="status" className="card p-4 text-sm text-gray-300">{t('findings.empty')}</p>
      ) : (
        <div className="space-y-4">
          {findings.map(finding => <FindingCard key={finding.id} finding={finding} />)}
        </div>
      )}
    </div>
  )
}
