import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { FileSearch, MessageSquareQuote, RefreshCw, ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react'
import { ApiError, type AgentRunStatus } from '../services/api'
import {
  useAgentRun,
  useAgentRunApprovals,
  useAgentRunVerifications,
  useApproveAgentRun,
  useAuditVerification,
  useCurrentRole,
  useVerifyAgentRun,
} from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'
import { Verdict, VERDICT_PRESENTATION } from '../components/Verdict'
import { ReviewChanges } from '../components/ReviewChanges'
import { RunCostCard } from '../components/CostSummary'
import { ReviewPanel } from '../components/ReviewPanel'
import { RunLogViewer } from '../components/RunLogViewer'
import { Spinner } from '../components/knowledge/PageStates'
import { EmptyState, PageHeader, Panel } from '../components/ui'
import { useRunChanges } from '../services/reviewChanges'
import { parseRouteId } from '../lib/routeId'
import { taskOfRun } from '../lib/taskEvidence'

function claimValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return String(value)
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

const SIGNATURE_STYLES = {
  valid: 'text-emerald-300 bg-emerald-950/60 border-emerald-700/50',
  invalid: 'text-red-300 bg-red-950/60 border-red-700/50',
  unsigned: 'text-amber-300 bg-amber-950/60 border-amber-700/50',
  legacy: 'text-gray-300 bg-gray-800 border-gray-600',
} as const

const SIGNATURE_ICONS = { valid: ShieldCheck, invalid: ShieldAlert, unsigned: ShieldQuestion, legacy: ShieldQuestion } as const

/** A check's outcome looks like the verdict it leads to: an icon and a word, never the colour alone. */
const CHECK_OUTCOME_LOOK: Record<string, AgentRunStatus> = { pass: 'verified', refuted: 'refuted', unverifiable: 'unverifiable' }

const SKELETON = 'skeleton motion-reduce:animate-none'

function CheckOutcome({ outcome }: { outcome: string }) {
  const { t } = useTranslation()
  const { className, Icon } = VERDICT_PRESENTATION[CHECK_OUTCOME_LOOK[outcome] ?? 'unverifiable']
  return (
    <span data-testid={`check-outcome-${outcome}`} className={`status-badge ${className}`}>
      <Icon size={11} aria-hidden="true" className="flex-shrink-0" />{t(`evidence.checkOutcome.${outcome}`)}
    </span>
  )
}

function RunDetail({ companyId, runId }: { companyId: number; runId: number }) {
  const { t } = useTranslation()
  const runQuery = useAgentRun(companyId, runId)
  const verificationsQuery = useAgentRunVerifications(companyId, runId)
  const approvalsQuery = useAgentRunApprovals(companyId, runId)
  const run = runQuery.data
  const externalRef = run?.external_ref ?? null
  const auditQuery = useAuditVerification(externalRef, companyId)
  const verifyMutation = useVerifyAgentRun(companyId, runId)
  const approveMutation = useApproveAgentRun(companyId, runId)
  const [approverName, setApproverName] = useState('')
  const [decision, setDecision] = useState<'approved' | 'rejected'>('approved')
  const [feedback, setFeedback] = useState('')
  const [category, setCategory] = useState('')
  const [formError, setFormError] = useState('')
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') === 'files' ? 'files' : 'overview'
  const changedFiles = useRunChanges(companyId, runId).data?.totals.files
  const role = useCurrentRole()
  const canReview = role === 'Owner' || role === 'Manager'

  const verifications = verificationsQuery.data?.verifications ?? []
  const latestVerification = verifications[verifications.length - 1]
  const audit = auditQuery.data

  function submitApproval(event: React.FormEvent) {
    event.preventDefault()
    if (decision === 'rejected' && !feedback.trim()) {
      setFormError(t('evidence.feedbackRequired'))
      return
    }
    if (!approverName.trim()) {
      setFormError(t('evidence.approverRequired'))
      return
    }
    setFormError('')
    approveMutation.mutate({
      company_id: companyId,
      approver_name: approverName.trim(),
      decision,
      ...(feedback.trim() ? { feedback: feedback.trim() } : {}),
      ...(decision === 'rejected' && category ? { rejection_category: category as 'scope' | 'quality' | 'security' | 'other' } : {}),
    })
  }

  if (runQuery.isLoading) {
    return <div role="status" className={`${SKELETON} h-48 rounded-xl`} aria-label={t('evidence.loading')} />
  }
  if (runQuery.isError || !run) {
    const notFound = runQuery.error instanceof ApiError && runQuery.error.status === 404
    return (
      <div className="space-y-3">
        <div role="alert" className="alert-error">{t(notFound ? 'evidence.notFound' : 'evidence.loadError', { id: runId })}</div>
        <div className="flex flex-wrap gap-2">
          {!notFound && (
            <button onClick={() => runQuery.refetch()} className="btn-tool">
              <RefreshCw size={13} aria-hidden="true" />{t('common.retry')}
            </button>
          )}
          <Link to="/evidence" className="btn-tool">{t('evidence.backToRuns')}</Link>
        </div>
      </div>
    )
  }

  const verdict: AgentRunStatus = latestVerification?.verdict ?? run.status
  const task = taskOfRun(run)

  return (
    <div className="space-y-4">
      <PageHeader
        breadcrumbs={[
          { label: t('nav.evidence', { defaultValue: 'Evidence' }), to: '/evidence' },
          { label: t('evidence.runCrumb', { id: runId, defaultValue: 'Run #{{id}}' }) },
        ]}
        title={t('evidence.detailTitle')}
        subtitle={<>
          <span className="block break-all font-mono text-xs text-gray-400">{run.external_ref} · {run.producer}</span>
          {task && (
            <span className="mt-1 block">
              {t('evidence.taskLabel', { defaultValue: 'Task' })}:{' '}
              <Link to={task.to} className="break-words font-medium text-brand-300 hover:underline">
                {task.title ?? t('evidence.taskFallback', { id: task.itemId, defaultValue: 'Task #{{id}}' })}
              </Link>
            </span>
          )}
        </>}
        actions={<Verdict status={verdict} />}
      />

      <RunCostCard companyId={companyId} runId={runId} />

      <div role="tablist" aria-label={t('review.tabs.label')} className="flex gap-1 border-b border-surface-700">
        {RUN_TABS.map(name => (
          <button key={name} type="button" role="tab" aria-selected={tab === name}
            onClick={() => setSearchParams(name === 'files' ? { tab: 'files' } : {}, { replace: true })}
            className={`focus-ring -mb-px inline-flex items-center gap-1.5 rounded-t-md border-b-2 px-3 py-2 text-[13px] font-medium transition-colors motion-reduce:transition-none ${
              tab === name ? 'border-brand-400 text-white' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            {t(`review.tabs.${name}`)}
            {name === 'files' && changedFiles !== undefined && <span className="count-pill">{changedFiles}</span>}
          </button>
        ))}
      </div>

      {tab === 'files' ? (
        <div className="space-y-4">
          <ReviewPanel companyId={companyId} runId={runId} canReview={canReview} />
          <ReviewChanges companyId={companyId} runId={runId} canComment={canReview} />
        </div>
      ) : <>
      {/* What the producer claimed and what the platform observed never share a surface: the claim is dashed
          under a speech mark, the observation solid under a shield, as on the board's cards. */}
      <section aria-labelledby="claim-observation-title" className="space-y-2">
        <h2 id="claim-observation-title" className="text-[13px] font-semibold text-gray-100">{t('evidence.claimVsObservation')}</h2>
        <div className="grid gap-3 lg:grid-cols-2">
          <Panel headingLevel={3} className="min-w-0 border-dashed border-surface-600 bg-transparent"
            title={<span className="inline-flex items-center gap-1.5"><MessageSquareQuote size={13} aria-hidden="true" className="text-gray-400" />{t('evidence.claimed')}</span>}>
            <p className="text-meta px-3 pt-2">{t('evidence.claimedHint')}</p>
            {run.claims && Object.keys(run.claims).length > 0 ? (
              <dl className="space-y-3 p-3">
                {Object.entries(run.claims).map(([key, value]) => (
                  <div key={key}>
                    <dt className="mb-0.5 font-mono text-[11px] text-gray-400">{key}</dt>
                    <dd className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-gray-300">{claimValue(value)}</dd>
                  </div>
                ))}
              </dl>
            ) : <p className="p-3 text-[13px] text-gray-400">{t('evidence.noClaims')}</p>}
          </Panel>
          <Panel headingLevel={3} className="min-w-0"
            title={<span className="inline-flex items-center gap-1.5"><ShieldCheck size={13} aria-hidden="true" className="text-emerald-300" />{t('evidence.observed')}</span>}>
            <p className="text-meta px-3 pt-2">{t('evidence.observedHint')}</p>
            {verificationsQuery.isLoading ? (
              <div className="p-3"><div className={`${SKELETON} h-20 rounded-md`} /></div>
            ) : latestVerification?.checks?.length ? (
              <ul className="data-list">
                {latestVerification.checks.map((check, index) => (
                  <li key={`${check.name}-${index}`} className="data-row">
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[13px] font-semibold text-gray-100">{check.name}</span>
                      <CheckOutcome outcome={check.outcome} />
                    </div>
                    <p className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-gray-300">{check.observation}</p>
                  </li>
                ))}
              </ul>
            ) : <p className="p-3 text-[13px] text-gray-400">{t('evidence.noObservations')}</p>}
            <div className="flex flex-wrap items-center gap-2 border-t border-surface-700 px-3 py-2">
              <span className="text-meta font-medium">{t('evidence.verdictLabel')}:</span>
              <Verdict status={verdict} compact />
              {latestVerification && <span className="text-meta tabular-nums">{new Date(latestVerification.observed_at).toLocaleString()}</span>}
            </div>
          </Panel>
        </div>
      </section>

      <RunLogViewer companyId={companyId} runId={runId} />

      <Panel title={t('evidence.verificationHistory')}
        actions={
          <button onClick={() => verifyMutation.mutate()} disabled={verifyMutation.isPending} className="btn-tool">
            {verifyMutation.isPending ? <Spinner size={13} /> : <ShieldCheck size={13} aria-hidden="true" />}
            {t('evidence.verify')}
          </button>
        }>
        {verifyMutation.isError && <p role="alert" className="px-3 pt-2.5 text-[13px] text-red-300">{t('evidence.verifyError')}</p>}
        {verificationsQuery.isError ? <p role="alert" className="px-3 py-2.5 text-[13px] text-red-300">{t('evidence.historyError')}</p> :
          verifications.length === 0 ? <p className="px-3 py-2.5 text-[13px] text-gray-400">{t('evidence.noVerifications')}</p> : (
            <ol className="data-list">
              {verifications.map((verification, index) => (
                <li key={verification.id} className="data-row">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <span className="text-meta tabular-nums">#{index + 1}</span>
                    <Verdict status={verification.verdict} compact />
                    <time className="text-meta tabular-nums">{new Date(verification.observed_at).toLocaleString()}</time>
                  </div>
                  {verification.checks?.map((check, checkIndex) => (
                    <p key={`${check.name}-${checkIndex}`} className="text-xs leading-relaxed text-gray-300">
                      <span className="font-mono font-semibold text-gray-200">{check.name}:</span> {check.observation}
                    </p>
                  ))}
                </li>
              ))}
            </ol>
          )}
      </Panel>

      <Panel title={t('evidence.approvalHistory')}>
        {approvalsQuery.isLoading ? <div className="p-3"><div className={`${SKELETON} h-16 rounded-md`} /></div> :
          approvalsQuery.isError ? <p role="alert" className="px-3 py-2.5 text-[13px] text-red-300">{t('evidence.historyError')}</p> :
            (approvalsQuery.data?.approvals.length ?? 0) === 0 ? <p className="px-3 py-2.5 text-[13px] text-gray-400">{t('evidence.noApprovals')}</p> : (
              <ol className="data-list">
                {approvalsQuery.data!.approvals.map(approval => (
                  <li key={approval.id} className="data-row">
                    <p className="text-[13px] font-semibold text-gray-100">
                      {t(`evidence.approval.${approval.decision}`)} · {approval.approver_name}
                    </p>
                    <time className="text-meta tabular-nums">{new Date(approval.decided_at).toLocaleString()}</time>
                    {approval.feedback && <p className="mt-1.5 max-w-prose whitespace-pre-wrap text-[13px] leading-relaxed text-gray-300">{approval.feedback}</p>}
                    {approval.rejection_category && <p className="text-meta mt-1">{t('evidence.category')}: {t(`evidence.categoryValues.${approval.rejection_category}`)}</p>}
                  </li>
                ))}
              </ol>
            )}
      </Panel>

      <Panel title={t('evidence.humanReview')}>
        <div className="space-y-4 p-3">
          {run.reviewed_by && (
            <aside className="rounded-md border border-dashed border-surface-600 p-3 text-[13px]" aria-label={t('evidence.suggestionTitle')}>
              <p className="flex items-center gap-1.5 text-xs font-semibold text-gray-400">
                <MessageSquareQuote size={12} aria-hidden="true" />{t('evidence.suggestionTitle')} · {run.reviewed_by}
              </p>
              {run.suggested_decision ? (
                <>
                  <p className="mt-1 font-semibold text-gray-200">
                    {t(`evidence.approval.${run.suggested_decision}`)}
                    {run.suggested_category ? ` · ${t(`evidence.categoryValues.${run.suggested_category}`)}` : ''}
                  </p>
                  {run.suggested_reason && <p className="mt-1 max-w-prose whitespace-pre-wrap leading-relaxed text-gray-300">{run.suggested_reason}</p>}
                </>
              ) : (
                <p className="mt-1 text-gray-400">{t('evidence.suggestionUnusable')}</p>
              )}
              <p className="text-meta mt-2">{t('evidence.suggestionNote')}</p>
            </aside>
          )}
          <form onSubmit={submitApproval} className="grid gap-3 md:grid-cols-2">
            <label className="text-xs text-gray-400">
              {t('evidence.approverName')}
              <input className="input mt-1 w-full" value={approverName} onChange={event => setApproverName(event.target.value)} />
            </label>
            <label className="text-xs text-gray-400">
              {t('evidence.decision')}
              <select
                className="input mt-1 w-full"
                value={decision}
                onChange={event => {
                  const nextDecision = event.target.value as 'approved' | 'rejected'
                  setDecision(nextDecision)
                  if (nextDecision === 'approved') setCategory('')
                  setFormError('')
                }}
              >
                <option value="approved">{t('evidence.approval.approved')}</option>
                <option value="rejected">{t('evidence.approval.rejected')}</option>
              </select>
            </label>
            <label className="text-xs text-gray-400 md:col-span-2">
              {t('evidence.feedback')}{decision === 'rejected' ? ' *' : ''}
              <textarea className="textarea mt-1 w-full" rows={3} value={feedback} onChange={event => { setFeedback(event.target.value); setFormError('') }} />
            </label>
            {decision === 'rejected' && (
              <label className="text-xs text-gray-400 md:col-span-2">
                {t('evidence.rejectionCategory')}
                <select className="input mt-1 w-full" value={category} onChange={event => setCategory(event.target.value)}>
                  <option value="">{t('evidence.noCategory')}</option>
                  {REJECTION_CATEGORIES.map(value => (
                    <option key={value} value={value}>{t(`evidence.categoryValues.${value}`)}</option>
                  ))}
                </select>
              </label>
            )}
            {formError && <p role="alert" className="text-[13px] text-red-300 md:col-span-2">{formError}</p>}
            {approveMutation.isError && <p role="alert" className="text-[13px] text-red-300 md:col-span-2">{t('evidence.approvalError')}</p>}
            {approveMutation.isSuccess && <p role="status" className="text-[13px] text-emerald-300 md:col-span-2">{t('evidence.approvalSaved')}</p>}
            <button type="submit" disabled={approveMutation.isPending} className="btn btn-primary btn-sm md:col-span-2 md:justify-self-start">
              {approveMutation.isPending && <Spinner />}
              {t('evidence.submitReview')}
            </button>
          </form>
        </div>
      </Panel>

      <Panel title={t('evidence.signatureStatus')}>
        <div className="space-y-3 p-3">
          {auditQuery.isLoading ? <div className={`${SKELETON} h-16 rounded-md`} /> :
            auditQuery.error instanceof ApiError && auditQuery.error.status === 404 ? (
              <p className="text-[13px] text-gray-400">{t('evidence.noAuditRows')}</p>
            ) : auditQuery.isError ? (
              <p role="alert" className="text-[13px] text-red-300">{t('evidence.auditError')}</p>
            ) : audit ? (
              <>
                <div className="flex flex-wrap gap-2">
                  {SIGNATURE_STATUSES.map(status => {
                    const Icon = SIGNATURE_ICONS[status]
                    return (
                      <span key={status} data-testid={`signature-${status}`} className={`status-badge tabular-nums ${SIGNATURE_STYLES[status]}`}>
                        <Icon size={12} aria-hidden="true" />
                        {t(`evidence.signature.${status}`)}: {audit.summary[status]}
                      </span>
                    )
                  })}
                </div>
                <h3 className="text-meta font-medium uppercase tracking-wider">{t('evidence.auditLineage')}</h3>
                {audit.rows.length === 0 ? <p className="text-[13px] text-gray-400">{t('evidence.noAuditRows')}</p> :
                  <ul className="data-list -mx-3 border-t border-surface-700">
                    {audit.rows.map(row => {
                      const Icon = SIGNATURE_ICONS[row.status]
                      return (
                        <li key={row.id} className="data-row flex flex-wrap items-center gap-2 py-2 text-[13px]">
                          <span className="font-mono text-xs tabular-nums text-gray-400">#{row.id}</span>
                          <span className="text-gray-200">{row.action}</span>
                          {row.legacy && (
                            <span data-testid="legacy-company" className={`status-badge ${SIGNATURE_STYLES.legacy}`}>
                              {t('evidence.legacyCompany')}
                            </span>
                          )}
                          <span data-testid={`signature-row-${row.status}`} className={`status-badge ${SIGNATURE_STYLES[row.status]}`}>
                            <Icon size={11} aria-hidden="true" />
                            {t(`evidence.signature.${row.status}`)}
                          </span>
                        </li>
                      )
                    })}
                  </ul>}
              </>
            ) : null}
          <p className="text-meta tabular-nums">
            {t('evidence.runAuditId')}: {run.audit_log_id ?? t('evidence.notRecorded')}
            {latestVerification?.audit_log_id !== undefined && latestVerification.audit_log_id !== null
              ? ` · ${t('evidence.latestVerificationAuditId')}: ${latestVerification.audit_log_id}` : ''}
          </p>
        </div>
      </Panel>
      </>}
    </div>
  )
}


const REJECTION_CATEGORIES = ['scope', 'quality', 'security', 'other'] as const
/** The run page's tabs: the evidence, and the files changed as a code review shows them. */
const RUN_TABS = ['overview', 'files'] as const
const SIGNATURE_STATUSES = ['valid', 'invalid', 'unsigned', 'legacy'] as const

export default function EvidenceDetail() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const { runId: rawRunId } = useParams()
  const runId = parseRouteId(rawRunId)

  if (companyId === null) {
    return <div className="page-container max-w-6xl"><EmptyState icon={FileSearch} title={t('evidence.selectCompany')} dashed /></div>
  }
  if (runId === null) {
    return <div className="page-container max-w-6xl space-y-3">
      <p role="alert" className="alert-error">{t('evidence.invalidId', { id: rawRunId })}</p>
      <Link to="/evidence" className="btn-tool">{t('evidence.backToRuns')}</Link>
    </div>
  }

  return <div className="page-container max-w-6xl"><RunDetail companyId={companyId} runId={runId} /></div>
}
