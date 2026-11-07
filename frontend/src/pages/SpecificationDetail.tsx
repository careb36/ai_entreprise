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
import { Check, CircleCheck, Loader2, Pencil, Send, WandSparkles } from 'lucide-react'
import { ArchitectureSummary } from '../components/ArchitectureSummary'
import { Breadcrumbs } from '../components/Breadcrumbs'
import { FindingList, Where } from '../components/Findings'
import { SpecificationEditor } from '../components/SpecificationEditor'
import { useCurrentRole } from '../hooks/useQueries'
import { parseRouteId } from '../lib/routeId'
import { errorDetail } from '../services/api'
import {
  type Coverage, type CoverageStatus, type Specification, type SpecificationBody, SpecificationConflict,
  useAnswerQuestion, useApproveSpecification, useDraftSpecification, useReviseSpecification, useSpecification, useUpdateSpecification,
} from '../services/specifications'
import { useAppStore } from '../store/useAppStore'
import { CheckSummary, StatusBadge, specificationPath } from './Specifications'

const COVERAGE_TONE: Record<CoverageStatus, string> = {
  uncovered: 'bg-gray-800 text-gray-400',
  planned: 'bg-sky-900/60 text-sky-300',
  blocked: 'bg-red-900/60 text-red-300',
  done: 'bg-emerald-900/60 text-emerald-300',
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
    <section aria-labelledby="specification-check" className="card p-4 space-y-3">
      <h2 id="specification-check" className="text-sm font-semibold text-white">{t('specifications.check.title')}</h2>
      {check.ok && (
        <p className="flex items-center gap-2 text-sm text-emerald-300">
          <CircleCheck size={15} aria-hidden="true" /> {t('specifications.check.ok')}
        </p>
      )}
      <FindingList findings={check.findings} severity="blocking" />
      <FindingList findings={check.findings} severity="warning" />
    </section>
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
    <li className="space-y-1">
      <p className="text-sm text-gray-200"><Where where={`Q${n}`} /> {question.question}</p>
      {editing ? (
        <form onSubmit={e => { e.preventDefault(); save() }} className="flex flex-wrap items-center gap-2">
          <input value={draft} onChange={e => setDraft(e.target.value)} className="input flex-1" aria-label={t('specifications.questions.answerLabel', { n })} />
          <button type="submit" disabled={answer.isPending} className="btn btn-primary btn-sm">
            {answer.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Send size={14} aria-hidden="true" />}
            {t('specifications.questions.save')}
          </button>
          <button type="button" onClick={() => { setEditing(false); setDraft(question.answer ?? '') }} className="btn btn-ghost btn-sm">{t('common.cancel')}</button>
          {answer.isError && <span role="alert" className="text-xs text-red-300">{t('specifications.questions.error', { message: errorDetail(answer.error) })}</span>}
        </form>
      ) : (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          {question.answer
            ? <span className="text-gray-300">{t('specifications.questions.answered', { answer: question.answer })}</span>
            : <span className="text-amber-300">{t('specifications.questions.unanswered')}</span>}
          {canAnswer && (
            <button type="button" onClick={() => setEditing(true)} className="btn btn-ghost btn-sm">
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
  return <span className={`badge ${COVERAGE_TONE[status]}`}>{t(`specifications.coverage.${status}`, { defaultValue: status })}</span>
}

function Requirements({ specification, coverage }: { specification: Specification; coverage: Coverage | undefined }) {
  const { t } = useTranslation()
  const byCriterion = new Map((coverage?.criteria ?? []).map(c => [c.id, c]))
  return (
    <section aria-labelledby="specification-requirements" className="space-y-2">
      <h2 id="specification-requirements" className="text-sm font-semibold text-white">{t('specifications.requirements.title')}</h2>
      {coverage && (
        <p className="text-xs text-gray-400">
          {t('specifications.coverage.summary', { covered: coverage.covered, total: coverage.total, done: coverage.done })}
        </p>
      )}
      {specification.requirements.length === 0 && <p className="text-sm text-gray-500">{t('specifications.requirements.none')}</p>}
      <ul className="space-y-2">
        {specification.requirements.map(requirement => (
          <li key={requirement.id} className="card p-4 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Where where={requirement.id} />
              <h3 className="text-sm font-semibold text-white">{requirement.title}</h3>
            </div>
            {requirement.description && <p className="text-sm text-gray-300 whitespace-pre-wrap">{requirement.description}</p>}
            <ul aria-label={t('specifications.requirements.criteriaOf', { id: requirement.id })} className="space-y-1.5">
              {requirement.criteria.map(criterion => {
                const covered = byCriterion.get(criterion.id)
                return (
                  <li key={criterion.id} className="flex flex-wrap items-start gap-2 text-xs text-gray-300">
                    <Where where={criterion.id} />
                    <span className="min-w-0 flex-1">
                      <span className="text-gray-500">{t('specifications.requirements.given')}</span> {criterion.given}
                      {' · '}<span className="text-gray-500">{t('specifications.requirements.when')}</span> {criterion.when}
                      {' · '}<span className="text-gray-500">{t('specifications.requirements.then')}</span> {criterion.then}
                    </span>
                    {covered && (
                      <span className="flex flex-wrap items-center gap-1">
                        <CoverageBadge status={covered.status} />
                        {covered.tasks.map(task => (
                          <Link key={task.id} to={`/sprints/${task.sprint_id}/tasks/${task.id}`} className="text-brand-300 hover:underline" title={task.title}>
                            {t('specifications.coverage.task', { id: task.id })}
                          </Link>
                        ))}
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
          </li>
        ))}
      </ul>
    </section>
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
      <section aria-label={t('specifications.actions.title')} className="card p-4 flex flex-wrap items-center gap-3">
        <button type="button" onClick={reviseApproved} disabled={revise.isPending} className="btn btn-secondary">
          {revise.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Pencil size={15} aria-hidden="true" />}
          {t('specifications.actions.revise')}
        </button>
        <span className="text-xs text-gray-500">{t('specifications.actions.reviseHint')}</span>
        {revise.isError && !(revise.error instanceof SpecificationConflict && revise.error.specificationId !== null) && (
          <span role="alert" className="text-sm text-red-300">{t('specifications.actions.reviseError', { message: errorDetail(revise.error) })}</span>
        )}
      </section>
    )
  }
  return (
    <section aria-label={t('specifications.actions.title')} className="card p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={onEdit} className="btn btn-secondary">
          <Pencil size={15} aria-hidden="true" /> {t('specifications.actions.edit')}
        </button>
        <button type="button" onClick={() => setAsking(open => !open)} className="btn btn-secondary" aria-expanded={asking}>
          <WandSparkles size={15} aria-hidden="true" /> {t('specifications.actions.revisePO')}
        </button>
        <button type="button" onClick={() => approve.mutate(specification.id)} disabled={!specification.check.ok || approve.isPending}
          title={specification.check.ok ? undefined : t('specifications.actions.approveBlocked')} className="btn btn-primary">
          {approve.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Check size={15} aria-hidden="true" />}
          {t('specifications.actions.approve')}
        </button>
        {!specification.check.ok && <span className="text-xs text-gray-500">{t('specifications.actions.approveBlocked')}</span>}
        {approve.isError && (
          <span role="alert" className="text-sm text-red-300">{t('specifications.actions.approveError', { message: errorDetail(approve.error) })}</span>
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
          className="space-y-2">
          {unanswered > 0 && <p className="text-xs text-amber-300">{t('specifications.actions.answerFirst', { count: unanswered })}</p>}
          <label className="block">
            <span className="input-label">{t('specifications.actions.reviseBrief')}</span>
            <textarea value={brief} onChange={e => setBrief(e.target.value)} rows={3} maxLength={4000} className="input w-full" />
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" disabled={redraft.isPending} className="btn btn-primary btn-sm">
              {redraft.isPending ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Send size={14} aria-hidden="true" />}
              {redraft.isPending ? t('specifications.drafting') : t('specifications.actions.sendToPO')}
            </button>
            {redraft.isError && <span role="alert" className="text-sm text-red-300">{t('specifications.draftError', { message: errorDetail(redraft.error) })}</span>}
          </div>
        </form>
      )}
    </section>
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
    return <div className="page-container max-w-4xl"><p role="alert" className="alert-error text-sm">{t('specifications.invalidId', { id })}</p></div>
  }
  if (query.isLoading) {
    return (
      <div className="page-container max-w-4xl" role="status">
        <p className="flex items-center gap-2 text-sm text-gray-400"><Loader2 size={15} className="animate-spin" aria-hidden="true" /> {t('specifications.loadingOne')}</p>
      </div>
    )
  }
  if (query.isError || !specification) {
    return (
      <div className="page-container max-w-4xl space-y-3">
        <div role="alert" className="alert-error flex flex-wrap items-center gap-2 text-sm">
          <span>{t('specifications.loadOneError', { message: query.error ? errorDetail(query.error) : '' })}</span>
          <button type="button" onClick={() => query.refetch()} className="underline">{t('common.retry')}</button>
        </div>
        <Link to="/company/specifications" className="btn btn-ghost btn-sm">{t('specifications.back')}</Link>
      </div>
    )
  }

  return (
    <div className="page-container max-w-4xl space-y-6">
      <Breadcrumbs items={[{ label: t('specifications.title'), to: '/company/specifications' }, { label: specification.title }]} />
      <div className="page-header">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="page-title">{specification.title}</h1>
            <span className="text-sm text-gray-400">{t('specifications.version', { version: specification.version })}</span>
            <StatusBadge status={specification.status} />
          </div>
          <p className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
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
          </p>
        </div>
      </div>

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
            <section aria-labelledby="specification-summary" className="card p-4">
              <h2 id="specification-summary" className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">{t('specifications.summary')}</h2>
              <p className="text-sm text-gray-200 whitespace-pre-wrap">{specification.summary}</p>
            </section>
          )}
          {canWrite && <Actions specification={specification} onEdit={() => setEditing(true)} />}
          <CheckPanel specification={specification} />
          <section aria-labelledby="specification-questions" className="card p-4 space-y-2">
            <h2 id="specification-questions" className="text-sm font-semibold text-white">{t('specifications.questions.title')}</h2>
            {specification.questions.length === 0 ? (
              <p className="text-sm text-gray-500">{t('specifications.questions.none')}</p>
            ) : (
              <ul className="space-y-3">
                {specification.questions.map((_, index) => (
                  <QuestionRow key={index} specification={specification} index={index} canAnswer={canWrite && specification.status === 'draft'} />
                ))}
              </ul>
            )}
          </section>
          <ArchitectureSummary specification={specification} canWrite={canWrite} />
          <Requirements specification={specification} coverage={specification.coverage} />
          <section aria-labelledby="specification-quality" className="card p-4 space-y-2">
            <h2 id="specification-quality" className="text-sm font-semibold text-white">{t('specifications.quality.title')}</h2>
            {specification.quality.length === 0 ? (
              <p className="text-sm text-gray-500">{t('specifications.quality.none')}</p>
            ) : (
              <ul className="space-y-1">
                {specification.quality.map(quality => (
                  <li key={quality.id} className="flex flex-wrap items-center gap-2 text-sm text-gray-200">
                    <Where where={quality.id} /><span>{quality.attribute}</span>
                    <span className="text-gray-400">{t('specifications.quality.targetIs', { target: quality.target })}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section aria-labelledby="specification-constraints" className="card p-4 space-y-2">
            <h2 id="specification-constraints" className="text-sm font-semibold text-white">{t('specifications.constraints.title')}</h2>
            {specification.constraints.length === 0 ? (
              <p className="text-sm text-gray-500">{t('specifications.constraints.none')}</p>
            ) : (
              <ul className="list-disc space-y-1 pl-5 text-sm text-gray-200">
                {specification.constraints.map((constraint, i) => <li key={i}>{constraint}</li>)}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  )
}
