import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Breadcrumbs } from '../components/Breadcrumbs'
import { FindingOutcomes, FindingStatusBadge, formatFindingDate } from '../components/FindingOutcomes'
import { useFinding } from '../hooks/useFindings'
import { parseRouteId } from '../lib/routeId'
import { ApiError, errorDetail } from '../services/api'

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-gray-400">{label}</dt>
      <dd className="min-w-0 break-words text-gray-200">{children}</dd>
    </>
  )
}

/**
 * One self-improvement finding (`/company/findings/:id`), read-only: its
 * source, target measure and specification (path, sha256 computed by the
 * platform, the text), and every cycle's outcome with its reasons and the B
 * and C shas.
 */
export default function CompanyFindingDetail() {
  const { t } = useTranslation()
  const { id } = useParams()
  const findingId = parseRouteId(id)
  const query = useFinding(findingId)
  const finding = query.data
  const notFound = findingId === null || (query.error instanceof ApiError && query.error.status === 404)

  return (
    <div className="page-container max-w-5xl space-y-6">
      <Breadcrumbs items={[
        { label: t('findings.title'), to: '/company/findings' },
        { label: finding?.title ?? t('findings.detail.crumb', { id: id ?? '' }) },
      ]} />

      {notFound ? (
        <p role="alert" className="alert-error text-sm">{t('findings.detail.notFound')}</p>
      ) : query.isPending ? (
        <div role="status" className="flex items-center gap-2 text-sm text-gray-400">
          <Loader2 size={15} className="animate-spin" aria-hidden="true" /> {t('findings.detail.loading')}
        </div>
      ) : query.isError ? (
        <div role="alert" className="alert-error flex flex-wrap items-center gap-2 text-sm">
          <span>{t('findings.detail.loadError', { message: errorDetail(query.error) })}</span>
          <button type="button" onClick={() => query.refetch()} className="underline">{t('common.retry')}</button>
        </div>
      ) : finding && (
        <>
          <div className="page-header">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="page-title">{finding.title}</h1>
              <FindingStatusBadge status={finding.status} />
            </div>
          </div>

          <section aria-labelledby="finding-record" className="card p-4 space-y-3">
            <h2 id="finding-record" className="text-sm font-semibold text-white">{t('findings.detail.record')}</h2>
            <dl className="grid gap-x-4 gap-y-1.5 text-xs sm:grid-cols-[max-content_1fr]">
              <Field label={t('findings.source')}>
                {finding.source ?? t('findings.noSource')}
                {finding.source_ref && <> · <code className="text-gray-300">{finding.source_ref}</code></>}
              </Field>
              <Field label={t('findings.targetMeasure')}>
                {finding.target_measure
                  ? <code className="whitespace-pre-wrap">{finding.target_measure}</code>
                  : t('findings.noTargetMeasure')}
              </Field>
              <Field label={t('findings.detail.specificationPath')}>
                {finding.specification_path ? <code>{finding.specification_path}</code> : '—'}
              </Field>
              <Field label={t('findings.detail.specificationSha')}>
                <code className="font-mono">{finding.specification_sha256}</code>
              </Field>
              <Field label={t('findings.detail.created')}>{formatFindingDate(finding.created_at)}</Field>
              <Field label={t('findings.detail.updated')}>{formatFindingDate(finding.updated_at)}</Field>
              {finding.closed_by && <Field label={t('findings.detail.closedBy')}>{finding.closed_by}</Field>}
            </dl>
          </section>

          <section aria-labelledby="finding-outcomes" className="card p-4 space-y-3">
            <h2 id="finding-outcomes" className="text-sm font-semibold text-white">{t('findings.detail.outcomes')}</h2>
            <p className="text-xs text-gray-400">{t('findings.detail.outcomesHint')}</p>
            <FindingOutcomes outcomes={finding.outcomes} label={t('findings.detail.outcomes')} />
          </section>

          <section aria-labelledby="finding-specification" className="card p-4 space-y-3">
            <h2 id="finding-specification" className="text-sm font-semibold text-white">{t('findings.detail.specification')}</h2>
            {finding.specification_text
              ? <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-surface-900 p-3 text-xs text-gray-300">{finding.specification_text}</pre>
              : <p className="text-xs text-gray-400">{t('findings.detail.noSpecificationText')}</p>}
          </section>
        </>
      )}
    </div>
  )
}
