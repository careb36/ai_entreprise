import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { FindingOutcomes, FindingStatusBadge, formatFindingDate } from '../components/FindingOutcomes'
import { LoadError, LoadingLine, PageHeader, Panel } from '../components/ui'
import { useFinding } from '../hooks/useFindings'
import { parseRouteId } from '../lib/routeId'
import { ApiError, errorDetail } from '../services/api'

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-meta sm:leading-[1.125rem]">{label}</dt>
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
  const crumb = finding?.title ?? t('findings.detail.crumb', { id: id ?? '' })

  return (
    <div className="page-container max-w-5xl space-y-4">
      <PageHeader
        breadcrumbs={[{ label: t('findings.title'), to: '/company/findings' }, { label: crumb }]}
        title={crumb}
        subtitle={finding && !notFound ? <FindingStatusBadge status={finding.status} /> : undefined}
      />

      {notFound ? (
        <p role="alert" className="alert-error">{t('findings.detail.notFound')}</p>
      ) : query.isPending ? (
        <LoadingLine>{t('findings.detail.loading')}</LoadingLine>
      ) : query.isError ? (
        <LoadError onRetry={() => query.refetch()}>{t('findings.detail.loadError', { message: errorDetail(query.error) })}</LoadError>
      ) : finding && (
        <>
          <Panel title={t('findings.detail.record')}>
            <dl className="grid gap-x-6 gap-y-2 p-3 text-xs sm:grid-cols-[max-content_1fr]">
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
              <Field label={t('findings.detail.created')}><span className="tabular-nums">{formatFindingDate(finding.created_at)}</span></Field>
              <Field label={t('findings.detail.updated')}><span className="tabular-nums">{formatFindingDate(finding.updated_at)}</span></Field>
              {finding.closed_by && <Field label={t('findings.detail.closedBy')}>{finding.closed_by}</Field>}
            </dl>
          </Panel>

          <Panel title={t('findings.detail.outcomes')}>
            <div className="space-y-2 p-3">
              <p className="max-w-prose text-xs text-gray-400">{t('findings.detail.outcomesHint')}</p>
              <FindingOutcomes outcomes={finding.outcomes} label={t('findings.detail.outcomes')} />
            </div>
          </Panel>

          <Panel title={t('findings.detail.specification')}>
            <div className="p-3">
              {finding.specification_text
                ? <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-md border border-surface-700 bg-surface-950/60 p-3 font-mono text-xs leading-relaxed text-gray-300">{finding.specification_text}</pre>
                : <p className="text-xs text-gray-400">{t('findings.detail.noSpecificationText')}</p>}
            </div>
          </Panel>
        </>
      )}
    </div>
  )
}
