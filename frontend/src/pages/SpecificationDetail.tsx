/**
 * One specification: its check (what blocks an approval, what only warns),
 * the PO's open questions a Manager answers, the requirements with their
 * acceptance criteria and which tasks cover each one, the quality attributes
 * and the constraints. A Manager edits a draft, asks the PO to revise it or
 * approves it; an approved one gets a new draft version with Revise.
 */
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Ban, CalendarClock, Check, CheckCircle, CircleCheck, CircleDashed, FlaskConical, Pencil, Send, ShieldCheck, ShieldX, Sparkles,
  type LucideIcon,
} from 'lucide-react'
import { ArchitectureSummary } from '../components/ArchitectureSummary'
import { FindingList, Where } from '../components/Findings'
import { SourcesConsulted } from '../components/SourcesConsulted'
import { SpecificationEditor } from '../components/SpecificationEditor'
import { CountPill, LoadError, LoadingLine, PageHeader, Panel, READABLE_TEXT, Spinner, StatusBadge as Badge, type StatusTone } from '../components/ui'
import { useCurrentRole } from '../hooks/useQueries'
import { parseRouteId } from '../lib/routeId'
import { errorDetail } from '../services/api'
import {
  type Coverage, type CoverageCriterion, type CoverageStatus, type Specification, type SpecificationBody, SpecificationConflict,
  useAnswerQuestion, useApproveSpecification, useDraftSpecification, useReviseSpecification, useSpecification, useUpdateSpecification,
} from '../services/specifications'
import { useAppStore } from '../store/useAppStore'
import { CheckSummary, StatusBadge, specificationPath } from './Specifications'

/** Each coverage status as an icon, a word and a tone: never the colour alone. */
const COVERAGE_LOOK: Record<CoverageStatus, { icon: LucideIcon; tone: StatusTone }> = {
  uncovered: { icon: CircleDashed, tone: 'neutral' },
  planned: { icon: CalendarClock, tone: 'info' },
  blocked: { icon: Ban, tone: 'danger' },
  done: { icon: CircleCheck, tone: 'success' },
}

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
  } catch {
    return iso
  }
}

function toBody(specification: Specification): SpecificationBody {
  return {
    title: specification.title,
    summary: specification.summary,
    requirements: specification.requirements.map(r => ({
      title: r.title, description: r.description, criteria: r.criteria.map(c => ({ given: c.given, when: c.when, then: c.then })),
    })),
    quality: specification.quality.map(q => ({ attribute: q.attribute, target: q.target })),
    constraints: [...specification.constraints],
  }
}

function CheckPanel({ specification }: { specification: Specification }) {
  const { t } = useTranslation()
  const { check } = specification
  return (
    <Panel title={t('specifications.check.title')}>
      <div className="space-y-3 p-3">
        {check.ok && (
          <p className="flex items-center gap-2 text-[13px] text-emerald-300">
            <CheckCircle size={14} aria-hidden="true" /> {t('specifications.check.ok')}
          </p>
        )}
        <FindingList findings={check.findings} severity="blocking" />
        <FindingList findings={check.findings} severity="warning" />
      </div>
    </Panel>
  )
}

function QuestionRow({ specification, index, canAnswer }: { specification: Specification; index: number; canAnswer: boolean }) {
  const { t } = useTranslation()
  const question = specification.questions[index]
  const answer = useAnswerQuestion()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(question.answer ?? '')
  const n = index + 1

  function save() {
    answer.mutate({ id: specification.id, index, answer: draft.trim() }, { onSuccess: () => setEditing(false) })
  }

  return (
    <li className="data-row space-y-1.5">
      <p className="text-[13px] text-gray-100"><Where where={`Q${n}`} /> {question.question}</p>
      {editing ? (
        <form onSubmit={e => { e.preventDefault(); save() }} className="flex flex-wrap items-center gap-2">
          <input value={draft} onChange={e => setDraft(e.target.value)} className="input control-sm flex-1" aria-label={t('specifications.questions.answerLabel', { n })} />
          <button type="submit" disabled={answer.isPending} className="btn btn-primary btn-sm">
            {answer.isPending ? <Spinner /> : <Send size={13} aria-hidden="true" />}
            {t('specifications.questions.save')}
          </button>
          <button type="button" onClick={() => { setEditing(false); setDraft(question.answer ?? '') }} className="btn-tool">{t('common.cancel')}</button>
          {answer.isError && <span role="alert" className="text-xs text-red-300">{t('specifications.questions.error', { message: errorDetail(answer.error) })}</span>}
        </form>
      ) : (
        <p className="flex flex-wrap items-center gap-2 text-xs">
          {question.answer
            ? <span className="text-gray-300">{t('specifications.questions.answered', { answer: question.answer })}</span>
            : <span className="text-amber-300">{t('specifications.questions.unanswered')}</span>}
          {canAnswer && (
            <button type="button" onClick={() => setEditing(true)} className="btn-tool h-7">
              <Pencil size={12} aria-hidden="true" /> {question.answer ? t('specifications.questions.edit') : t('specifications.questions.answer')}
            </button>
          )}
        </p>
      )}
    </li>
  )
}

function CoverageBadge({ status }: { status: CoverageStatus }) {
  const { t } = useTranslation()
  const look = COVERAGE_LOOK[status] ?? COVERAGE_LOOK.uncovered
  return <Badge icon={look.icon} tone={look.tone} label={t(`specifications.coverage.${status}`, { defaultValue: status })} />
}

/**
 * The criterion's acceptance test, as the latest observed run saw it: verified
 * only when it passed. An observation, so it carries the shield the board's
 * observed signals carry.
 */
function TestBadge({ criterion }: { criterion: CoverageCriterion }) {
  const { t } = useTranslation()
  const tests = criterion.tests ?? []
  if (!criterion.last_result && tests.length === 0) return null
  const result = criterion.last_result
  const tone = criterion.verified
    ? 'border-emerald-700/50 bg-emerald-950/50 text-emerald-300'
    : result === 'fail' ? 'border-red-700/50 bg-red-950/50 text-red-300' : 'border-surface-700 bg-surface-800 text-gray-300'
  const Icon = criterion.verified ? ShieldCheck : result === 'fail' ? ShieldX : FlaskConical
  const label = criterion.verified
    ? t('specifications.coverage.verified')
    : result
      ? t(`specifications.coverage.testResult.${result}`, { defaultValue: result })
      : t('specifications.coverage.testPlanned')
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className={`status-badge ${tone}`} title={criterion.test}>
        <Icon size={11} aria-hidden="true" className="flex-shrink-0" />{label}
      </span>
      {tests.map(task => (
        <Link key={task.id} to={`/sprints/${task.sprint_id}/tasks/${task.id}`} className="text-brand-300 hover:underline" title={task.title}>
          {t('specifications.coverage.testTask', { id: task.id })}
        </Link>
      ))}
    </span>
  )
}

function Requirements({ specification, coverage }: { specification: Specification; coverage: Coverage | undefined }) {
  const { t } = useTranslation()
  const byCriterion = new Map((coverage?.criteria ?? []).map(c => [c.id, c]))
  return (
    <Panel title={t('specifications.requirements.title')}
      count={<CountPill count={specification.requirements.length} label={t('specifications.requirements.countLabel', { count: specification.requirements.length })} />}
      actions={coverage && (
        <p className="text-meta tabular-nums">
          {t('specifications.coverage.summary', { covered: coverage.covered, total: coverage.total, done: coverage.done })}
          {' · '}
          {t('specifications.coverage.verifiedSummary', { verified: coverage.verified ?? 0, total: coverage.total })}
        </p>
      )}>
      {specification.requirements.length === 0 && <p className="px-3 py-2.5 text-[13px] text-gray-400">{t('specifications.requirements.none')}</p>}
      <ul className="data-list">
        {specification.requirements.map(requirement => (
          <li key={requirement.id} className="data-row space-y-2 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <Where where={requirement.id} />
              <h3 className="text-[13px] font-semibold text-gray-100">{requirement.title}</h3>
            </div>
            {requirement.description && <p className={READABLE_TEXT}>{requirement.description}</p>}
            <ul aria-label={t('specifications.requirements.criteriaOf', { id: requirement.id })} className="space-y-2 border-l border-surface-700 pl-3">
              {requirement.criteria.map(criterion => {
                const covered = byCriterion.get(criterion.id)
                return (
                  <li key={criterion.id} className="flex flex-wrap items-start gap-2 text-xs text-gray-300">
                    <Where where={criterion.id} />
                    <span className="min-w-0 max-w-prose flex-1 leading-relaxed">
                      <span className="font-medium text-gray-400">{t('specifications.requirements.given')}</span> {criterion.given}
                      {' · '}<span className="font-medium text-gray-400">{t('specifications.requirements.when')}</span> {criterion.when}
                      {' · '}<span className="font-medium text-gray-400">{t('specifications.requirements.then')}</span> {criterion.then}
                    </span>
                    {covered && (
                      <span className="flex flex-wrap items-center gap-1.5">
                        <CoverageBadge status={covered.status} />
                        {covered.tasks.map(task => (
                          <Link key={task.id} to={`/sprints/${task.sprint_id}/tasks/${task.id}`} className="text-brand-300 hover:underline" title={task.title}>
                            {t('specifications.coverage.task', { id: task.id })}
                          </Link>
                        ))}
                        <TestBadge criterion={covered} />
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function Actions({ specification, onEdit }: { specification: Specification; onEdit: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const approve = useApproveSpecification()
  const revise = useReviseSpecification()
  const redraft = useDraftSpecification()
  const [brief, setBrief] = useState('')
  const [asking, setAsking] = useState(false)
  const unanswered = specification.questions.filter(q => !q.answer).length
  const conflictFindings = approve.error instanceof SpecificationConflict ? approve.error.findings : []

  function reviseApproved() {
    revise.mutate(specification.id, {
      onSuccess: draft => navigate(specificationPath(draft.id)),
      onError: error => {
        if (error instanceof SpecificationConflict && error.specificationId !== null) navigate(specificationPath(error.specificationId))
      },
    })
  }

  if (specification.status === 'superseded') return null
  if (specification.status === 'approved') {
    return (
      <section aria-label={t('specifications.actions.title')} className="panel flex flex-wrap items-center gap-3 p-3">
        <button type="button" onClick={reviseApproved} disabled={revise.isPending} className="btn-tool">
          {revise.isPending ? <Spinner /> : <Pencil size={13} aria-hidden="true" />}
          {t('specifications.actions.revise')}
        </button>
        <span className="text-xs text-gray-400">{t('specifications.actions.reviseHint')}</span>
        {revise.isError && !(revise.error instanceof SpecificationConflict && revise.error.specificationId !== null) && (
          <span role="alert" className="text-[13px] text-red-300">{t('specifications.actions.reviseError', { message: errorDetail(revise.error) })}</span>
        )}
      </section>
    )
  }
  return (
    <section aria-label={t('specifications.actions.title')} className="panel space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={onEdit} className="btn-tool">
          <Pencil size={13} aria-hidden="true" /> {t('specifications.actions.edit')}
        </button>
        <button type="button" onClick={() => setAsking(open => !open)} className={`btn-tool ${asking ? 'btn-tool-on' : ''}`} aria-expanded={asking}>
          <Sparkles size={13} aria-hidden="true" /> {t('specifications.actions.revisePO')}
        </button>
        <button type="button" onClick={() => approve.mutate(specification.id)} disabled={!specification.check.ok || approve.isPending}
          title={specification.check.ok ? undefined : t('specifications.actions.approveBlocked')} className="btn btn-primary btn-sm">
          {approve.isPending ? <Spinner /> : <Check size={14} aria-hidden="true" />}
          {t('specifications.actions.approve')}
        </button>
        {!specification.check.ok && <span className="text-xs text-gray-400">{t('specifications.actions.approveBlocked')}</span>}
        {approve.isError && (
          <span role="alert" className="text-[13px] text-red-300">{t('specifications.actions.approveError', { message: errorDetail(approve.error) })}</span>
        )}
      </div>
      {conflictFindings.length > 0 && (
        <div className="space-y-2">
          <FindingList findings={conflictFindings} severity="blocking" />
          <FindingList findings={conflictFindings} severity="warning" />
        </div>
      )}
      {asking && (
        <form onSubmit={e => { e.preventDefault(); redraft.mutate({ company_id: specification.company_id, brief: brief.trim(), specification_id: specification.id }, { onSuccess: () => { setAsking(false); setBrief('') } }) }}
          className="space-y-2 border-t border-surface-700 pt-3">
          {unanswered > 0 && <p className="text-xs text-amber-300">{t('specifications.actions.answerFirst', { count: unanswered })}</p>}
          <label className="block">
            <span className="input-label">{t('specifications.actions.reviseBrief')}</span>
            <textarea value={brief} onChange={e => setBrief(e.target.value)} rows={3} maxLength={4000} className="input w-full" />
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" disabled={redraft.isPending} className="btn btn-primary btn-sm">
              {redraft.isPending ? <Spinner /> : <Send size={13} aria-hidden="true" />}
              {redraft.isPending ? t('specifications.drafting') : t('specifications.actions.sendToPO')}
            </button>
            {redraft.isError && <span role="alert" className="text-[13px] text-red-300">{t('specifications.draftError', { message: errorDetail(redraft.error) })}</span>}
          </div>
        </form>
      )}
    </section>
  )
}

/** A short list section (quality attributes, constraints) with its empty line. */
function ListPanel({ title, empty, count, children }: { title: string; empty: string; count: number; children: React.ReactNode }) {
  return (
    <Panel title={title}>
      {count === 0 ? <p className="px-3 py-2.5 text-[13px] text-gray-400">{empty}</p> : children}
    </Panel>
  )
}

export default function SpecificationDetail() {
  const { t } = useTranslation()
  const { id } = useParams<{ id: string }>()
  const specificationId = parseRouteId(id)
  const query = useSpecification(specificationId)
  const role = useCurrentRole()
  const companyId = useAppStore(state => state.companyId)
  const update = useUpdateSpecification()
  const [editing, setEditing] = useState(false)
  const specification = query.data
  const canWrite = (role === 'Owner' || role === 'Manager') && specification?.company_id === companyId
  if (specificationId === null) {
    return <div className="page-container max-w-4xl"><p role="alert" className="alert-error">{t('specifications.invalidId', { id })}</p></div>
  }
  if (query.isLoading) {
    return <div className="page-container max-w-4xl"><LoadingLine>{t('specifications.loadingOne')}</LoadingLine></div>
  }
  if (query.isError || !specification) {
    return (
      <div className="page-container max-w-4xl space-y-3">
        <LoadError onRetry={() => query.refetch()}>{t('specifications.loadOneError', { message: query.error ? errorDetail(query.error) : '' })}</LoadError>
        <Link to="/company/specifications" className="btn-tool">{t('specifications.back')}</Link>
      </div>
    )
  }

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader
        breadcrumbs={[{ label: t('specifications.title'), to: '/company/specifications' }, { label: specification.title }]}
        title={specification.title}
        subtitle={(
          <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs">
            <span className="tabular-nums text-gray-300">{t('specifications.version', { version: specification.version })}</span>
            <StatusBadge status={specification.status} />
            {specification.drafted_by && <span>{t('specifications.draftedBy', { who: specification.drafted_by })}</span>}
            {specification.approved_by && specification.approved_at && (
              <span>{t('specifications.approvedBy', { who: specification.approved_by, date: formatDate(specification.approved_at) })}</span>
            )}
            {specification.supersedes_id !== null && (
              <Link to={specificationPath(specification.supersedes_id)} className="text-brand-300 hover:underline">
                {t('specifications.supersedes', { id: specification.supersedes_id })}
              </Link>
            )}
            <CheckSummary check={specification.check} />
          </span>
        )}
      />

      {editing ? (
        <SpecificationEditor
          initial={toBody(specification)}
          saving={update.isPending}
          error={update.isError ? errorDetail(update.error) : null}
          onCancel={() => { update.reset(); setEditing(false) }}
          onSave={body => update.mutate({ id: specification.id, body }, { onSuccess: () => setEditing(false) })}
        />
      ) : (
        <>
          {specification.summary && (
            <Panel title={t('specifications.summary')}>
              <p className={`${READABLE_TEXT} p-3`}>{specification.summary}</p>
            </Panel>
          )}
          {canWrite && <Actions specification={specification} onEdit={() => setEditing(true)} />}
          <CheckPanel specification={specification} />
          <SourcesConsulted sources={specification.sources ?? []} headingId="specification-sources" />
          <Panel title={t('specifications.questions.title')}
            count={specification.questions.length > 0
              ? <CountPill count={specification.questions.length} label={t('specifications.questions.countLabel', { count: specification.questions.length })} />
              : undefined}>
            {specification.questions.length === 0 ? (
              <p className="px-3 py-2.5 text-[13px] text-gray-400">{t('specifications.questions.none')}</p>
            ) : (
              <ul className="data-list">
                {specification.questions.map((_, index) => (
                  <QuestionRow key={index} specification={specification} index={index} canAnswer={canWrite && specification.status === 'draft'} />
                ))}
              </ul>
            )}
          </Panel>
          <ArchitectureSummary specification={specification} canWrite={canWrite} />
          <Requirements specification={specification} coverage={specification.coverage} />
          <ListPanel title={t('specifications.quality.title')} empty={t('specifications.quality.none')} count={specification.quality.length}>
            <ul className="data-list">
              {specification.quality.map(quality => (
                <li key={quality.id} className="data-row flex flex-wrap items-center gap-2 py-2 text-[13px] text-gray-200">
                  <Where where={quality.id} /><span>{quality.attribute}</span>
                  <span className="text-xs text-gray-400">{t('specifications.quality.targetIs', { target: quality.target })}</span>
                </li>
              ))}
            </ul>
          </ListPanel>
          <ListPanel title={t('specifications.constraints.title')} empty={t('specifications.constraints.none')} count={specification.constraints.length}>
            <ul className="max-w-prose list-disc space-y-1 py-2.5 pl-8 pr-3 text-[13px] leading-relaxed text-gray-200">
              {specification.constraints.map((constraint, i) => <li key={i}>{constraint}</li>)}
            </ul>
          </ListPanel>
        </>
      )}
    </div>
  )
}
