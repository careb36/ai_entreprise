/**
 * One architecture of a specification: its check (what blocks an approval,
 * what only warns), the Architect's open questions a Manager answers, the
 * modules with what they depend on and cover, the interfaces with their
 * contracts, the decisions and the conventions. A Manager edits a draft,
 * asks the Architect to revise it or approves it; an approved one gets a new
 * draft version with Revise, and is written to the repository as
 * `docs/ARCHITECTURE.md` through the workspace daemon. The `new` route
 * renders the editor for an architecture a person writes.
 */
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Check, ChevronDown, ChevronRight, CheckCircle, FileText, FolderOutput, Loader2, Pencil, Send, Sparkles } from 'lucide-react'
import { ArchitectureEditor } from '../components/ArchitectureEditor'
import { Breadcrumbs } from '../components/Breadcrumbs'
import { FindingList, Where } from '../components/Findings'
import { SourcesConsulted } from '../components/SourcesConsulted'
import { WorkspaceDaemonHelp } from '../components/WorkspaceDaemonHelp'
import { useCurrentRole } from '../hooks/useQueries'
import { parseRouteId } from '../lib/routeId'
import { ApiError, errorDetail } from '../services/api'
import {
  type Architecture, type ArchitectureBody, ArchitectureConflict, architecturePath, architecturesApi,
  useAnswerArchitectureQuestion, useApproveArchitecture, useArchitecture, useCreateArchitecture, useDraftArchitecture,
  useReviseArchitecture, useUpdateArchitecture, useWriteArchitecture,
} from '../services/architectures'
import { type Specification, useSpecification } from '../services/specifications'
import { useAppStore } from '../store/useAppStore'
import { CheckSummary, StatusBadge, specificationPath } from './Specifications'

const DECISION_FIELDS = ['context', 'decision', 'consequences'] as const

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
  } catch {
    return iso
  }
}

function toBody(architecture: Architecture): ArchitectureBody {
  const nameOf = new Map(architecture.modules.map(m => [m.id, m.name]))
  const names = (ids: string[]) => ids.map(id => nameOf.get(id) ?? id)
  return {
    title: architecture.title,
    overview: architecture.overview,
    modules: architecture.modules.map(m => ({
      name: m.name, responsibility: m.responsibility, path: m.path, depends_on: names(m.depends_on), covers: [...m.covers],
    })),
    interfaces: architecture.interfaces.map(i => ({
      name: i.name, kind: i.kind, module: nameOf.get(i.module_id) ?? i.module_id, contract: i.contract, consumers: names(i.consumers),
    })),
    decisions: architecture.decisions.map(d => ({ title: d.title, context: d.context, decision: d.decision, consequences: d.consequences })),
    conventions: [...architecture.conventions],
  }
}

function ModuleChip({ id, architecture }: { id: string; architecture: Architecture }) {
  const name = architecture.modules.find(m => m.id === id)?.name
  return <span className="inline-flex items-center gap-1"><Where where={id} />{name && <span className="text-xs text-gray-400">{name}</span>}</span>
}

function CheckPanel({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  const { check } = architecture
  return (
    <section aria-labelledby="architecture-check" className="card p-4 space-y-3">
      <h2 id="architecture-check" className="text-sm font-semibold text-white">{t('specifications.check.title')}</h2>
      {check.ok && (
        <p className="flex items-center gap-2 text-sm text-emerald-300">
          <CheckCircle size={15} aria-hidden="true" /> {t('architectures.check.ok')}
        </p>
      )}
      <FindingList findings={check.findings} severity="blocking" />
      <FindingList findings={check.findings} severity="warning" />
    </section>
  )
}

function QuestionRow({ architecture, index, canAnswer }: { architecture: Architecture; index: number; canAnswer: boolean }) {
  const { t } = useTranslation()
  const question = architecture.questions[index]
  const answer = useAnswerArchitectureQuestion()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(question.answer ?? '')
  const n = index + 1

  function save() {
    answer.mutate({ id: architecture.id, index, answer: draft.trim() }, { onSuccess: () => setEditing(false) })
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

function Modules({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  return (
    <section aria-labelledby="architecture-modules" className="space-y-2">
      <h2 id="architecture-modules" className="text-sm font-semibold text-white">{t('architectures.modules.title')}</h2>
      {architecture.modules.length === 0 && <p className="text-sm text-gray-500">{t('architectures.modules.none')}</p>}
      <ul className="space-y-2">
        {architecture.modules.map(module => (
          <li key={module.id} className="card p-4 space-y-2" aria-label={module.id}>
            <div className="flex flex-wrap items-center gap-2">
              <Where where={module.id} />
              <h3 className="text-sm font-semibold text-white">{module.name}</h3>
              <code className="font-mono text-xs text-gray-400">{module.path}</code>
            </div>
            {module.responsibility && <p className="text-sm text-gray-300 whitespace-pre-wrap">{module.responsibility}</p>}
            {module.depends_on.length > 0 && (
              <p className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
                <span>{t('architectures.modules.dependsOn')}</span>
                {module.depends_on.map(id => <ModuleChip key={id} id={id} architecture={architecture} />)}
              </p>
            )}
            {module.covers.length > 0 && (
              <p className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
                <span>{t('architectures.modules.covers')}</span>
                {module.covers.map(id => (
                  <Link key={id} to={specificationPath(architecture.specification_id)} className="hover:underline"><Where where={id} /></Link>
                ))}
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Interfaces({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  return (
    <section aria-labelledby="architecture-interfaces" className="space-y-2">
      <h2 id="architecture-interfaces" className="text-sm font-semibold text-white">{t('architectures.interfaces.title')}</h2>
      {architecture.interfaces.length === 0 && <p className="text-sm text-gray-500">{t('architectures.interfaces.none')}</p>}
      <ul className="space-y-2">
        {architecture.interfaces.map(iface => (
          <li key={iface.id} className="card p-4 space-y-2" aria-label={iface.id}>
            <div className="flex flex-wrap items-center gap-2">
              <Where where={iface.id} />
              <h3 className="text-sm font-semibold text-white">{iface.name}</h3>
              <span className="badge bg-sky-900/60 text-sky-300">{t(`architectures.kinds.${iface.kind}`, { defaultValue: iface.kind })}</span>
            </div>
            <p className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
              <span>{t('architectures.interfaces.providedBy')}</span>
              <ModuleChip id={iface.module_id} architecture={architecture} />
              {iface.consumers.length > 0 && <span>{t('architectures.interfaces.consumers')}</span>}
              {iface.consumers.map(id => <ModuleChip key={id} id={id} architecture={architecture} />)}
            </p>
            {iface.contract && <pre className="overflow-x-auto rounded bg-surface-900/80 p-2 font-mono text-xs text-gray-200 whitespace-pre-wrap">{iface.contract}</pre>}
          </li>
        ))}
      </ul>
    </section>
  )
}

function Decisions({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  return (
    <section aria-labelledby="architecture-decisions" className="space-y-2">
      <h2 id="architecture-decisions" className="text-sm font-semibold text-white">{t('architectures.decisions.title')}</h2>
      {architecture.decisions.length === 0 && <p className="text-sm text-gray-500">{t('architectures.decisions.none')}</p>}
      <ul className="space-y-2">
        {architecture.decisions.map(decision => (
          <li key={decision.id} className="card p-4 space-y-2" aria-label={decision.id}>
            <div className="flex flex-wrap items-center gap-2">
              <Where where={decision.id} />
              <h3 className="text-sm font-semibold text-white">{decision.title}</h3>
            </div>
            <dl className="space-y-1 text-sm">
              {DECISION_FIELDS.map(field => decision[field] && (
                <div key={field}>
                  <dt className="text-xs font-semibold uppercase tracking-wider text-gray-500">{t(`architectures.decisions.${field}`, { defaultValue: field })}</dt>
                  <dd className="text-gray-300 whitespace-pre-wrap">{decision[field]}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
    </section>
  )
}

/** The Markdown the repository gets, fetched with the token and shown in a collapsible block. */
function MarkdownView({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function show() {
    if (open) { setOpen(false); return }
    setOpen(true)
    if (text !== null) return
    setLoading(true)
    setError(null)
    try {
      setText(await architecturesApi.document(architecture.id))
    } catch (err) {
      setError(errorDetail(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <section aria-label={t('architectures.markdown.title')} className="space-y-2">
      <button type="button" onClick={show} aria-expanded={open} className="btn btn-ghost btn-sm">
        {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
        <FileText size={14} aria-hidden="true" /> {t('architectures.markdown.view')}
      </button>
      {open && loading && <p role="status" className="text-xs text-gray-400">{t('architectures.markdown.loading')}</p>}
      {open && error && <p role="alert" className="text-sm text-red-300">{t('architectures.markdown.error', { message: error })}</p>}
      {open && text !== null && <pre className="overflow-x-auto rounded bg-surface-900/80 p-3 font-mono text-xs text-gray-200 whitespace-pre-wrap">{text}</pre>}
    </section>
  )
}

function Actions({ architecture, onEdit }: { architecture: Architecture; onEdit: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const approve = useApproveArchitecture()
  const revise = useReviseArchitecture()
  const redraft = useDraftArchitecture()
  const write = useWriteArchitecture()
  const specId = architecture.specification_id
  const unanswered = architecture.questions.filter(q => !q.answer).length
  const conflictFindings = approve.error instanceof ArchitectureConflict ? approve.error.findings : []

  function reviseApproved() {
    revise.mutate(architecture.id, {
      onSuccess: draft => navigate(architecturePath(specId, draft.id)),
      onError: error => {
        if (error instanceof ArchitectureConflict && error.architectureId !== null) navigate(architecturePath(specId, error.architectureId))
      },
    })
  }

  if (architecture.status === 'superseded') return null
  if (architecture.status === 'approved') {
    const daemonDown = write.error instanceof ApiError && write.error.status === 503 ? write.error : null
    return (
      <section aria-label={t('specifications.actions.title')} className="card p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <button type="button" onClick={() => write.mutate(architecture.id)} disabled={write.isPending} className="btn btn-primary">
            {write.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <FolderOutput size={15} aria-hidden="true" />}
            {write.isPending ? t('architectures.actions.writing') : t('architectures.actions.write')}
          </button>
          <button type="button" onClick={reviseApproved} disabled={revise.isPending} className="btn btn-secondary">
            {revise.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Pencil size={15} aria-hidden="true" />}
            {t('specifications.actions.revise')}
          </button>
          <span className="text-xs text-gray-500">{t('architectures.actions.reviseHint')}</span>
          {revise.isError && !(revise.error instanceof ArchitectureConflict && revise.error.architectureId !== null) && (
            <span role="alert" className="text-sm text-red-300">{t('specifications.actions.reviseError', { message: errorDetail(revise.error) })}</span>
          )}
        </div>
        {write.isSuccess && write.data.written_path && (
          <p role="status" className="flex items-center gap-2 text-sm text-emerald-300">
            <CheckCircle size={15} aria-hidden="true" />
            {t('architectures.actions.written', { path: write.data.written_path, hash: (write.data.written_sha256 ?? '').slice(0, 12) })}
          </p>
        )}
        {write.isError && (daemonDown
          ? <WorkspaceDaemonHelp detail={daemonDown.detail} />
          : <p role="alert" className="text-sm text-red-300">{t('architectures.actions.writeError', { message: errorDetail(write.error) })}</p>)}
      </section>
    )
  }
  return (
    <section aria-label={t('specifications.actions.title')} className="card p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={onEdit} className="btn btn-secondary">
          <Pencil size={15} aria-hidden="true" /> {t('specifications.actions.edit')}
        </button>
        <button type="button" disabled={redraft.isPending} className="btn btn-secondary"
          onClick={() => redraft.mutate({ specification_id: specId, architecture_id: architecture.id })}>
          {redraft.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Sparkles size={15} aria-hidden="true" />}
          {redraft.isPending ? t('architectures.drafting') : t('architectures.actions.reviseArchitect')}
        </button>
        <button type="button" onClick={() => approve.mutate(architecture.id)} disabled={!architecture.check.ok || approve.isPending}
          title={architecture.check.ok ? undefined : t('specifications.actions.approveBlocked')} className="btn btn-primary">
          {approve.isPending ? <Loader2 size={15} className="animate-spin" aria-hidden="true" /> : <Check size={15} aria-hidden="true" />}
          {t('specifications.actions.approve')}
        </button>
        {!architecture.check.ok && <span className="text-xs text-gray-500">{t('specifications.actions.approveBlocked')}</span>}
        {approve.isError && (
          <span role="alert" className="text-sm text-red-300">{t('specifications.actions.approveError', { message: errorDetail(approve.error) })}</span>
        )}
        {redraft.isError && <span role="alert" className="text-sm text-red-300">{t('architectures.draftError', { message: errorDetail(redraft.error) })}</span>}
      </div>
      {unanswered > 0 && <p className="text-xs text-amber-300">{t('specifications.actions.answerFirst', { count: unanswered })}</p>}
      {conflictFindings.length > 0 && (
        <div className="space-y-2">
          <FindingList findings={conflictFindings} severity="blocking" />
          <FindingList findings={conflictFindings} severity="warning" />
        </div>
      )}
    </section>
  )
}

function NewArchitecture({ specification }: { specification: Specification }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const create = useCreateArchitecture()
  return (
    <div className="page-container max-w-4xl space-y-6">
      <Breadcrumbs items={[
        { label: t('specifications.title'), to: '/company/specifications' },
        { label: specification.title, to: specificationPath(specification.id) },
        { label: t('architectures.editor.newTitle') },
      ]} />
      <h1 className="page-title">{t('architectures.editor.newTitle')}</h1>
      <ArchitectureEditor
        requirements={specification.requirements}
        saving={create.isPending}
        error={create.isError ? errorDetail(create.error) : null}
        onCancel={() => navigate(specificationPath(specification.id))}
        onSave={body => create.mutate({ specification_id: specification.id, ...body }, { onSuccess: a => navigate(architecturePath(specification.id, a.id)) })}
      />
    </div>
  )
}

export default function ArchitectureDetail() {
  const { t } = useTranslation()
  const { specId, id } = useParams<{ specId: string; id?: string }>()
  const specificationId = parseRouteId(specId)
  const isNew = id === undefined
  const architectureId = isNew ? null : parseRouteId(id)
  const specQuery = useSpecification(specificationId)
  const query = useArchitecture(architectureId)
  const role = useCurrentRole()
  const companyId = useAppStore(state => state.companyId)
  const update = useUpdateArchitecture()
  const [editing, setEditing] = useState(false)
  const specification = specQuery.data
  const architecture = query.data
  const canWrite = (role === 'Owner' || role === 'Manager') && specification?.company_id === companyId
  const invalidId = specificationId === null || (!isNew && architectureId === null)
  if (invalidId) {
    return <div className="page-container max-w-4xl"><p role="alert" className="alert-error text-sm">{t('architectures.invalidId', { id: id ?? specId })}</p></div>
  }
  if (specQuery.isLoading || (!isNew && query.isLoading)) {
    return (
      <div className="page-container max-w-4xl" role="status">
        <p className="flex items-center gap-2 text-sm text-gray-400"><Loader2 size={15} className="animate-spin" aria-hidden="true" /> {t('architectures.loadingOne')}</p>
      </div>
    )
  }
  const failed = specQuery.isError ? specQuery : query.isError ? query : null
  if (failed || !specification || (!isNew && !architecture)) {
    return (
      <div className="page-container max-w-4xl space-y-3">
        <div role="alert" className="alert-error flex flex-wrap items-center gap-2 text-sm">
          <span>{t('architectures.loadOneError', { message: failed?.error ? errorDetail(failed.error) : '' })}</span>
          <button type="button" onClick={() => { specQuery.refetch(); query.refetch() }} className="underline">{t('common.retry')}</button>
        </div>
        <Link to={specificationPath(specificationId)} className="btn btn-ghost btn-sm">{t('architectures.back')}</Link>
      </div>
    )
  }
  if (isNew || !architecture) {
    if (!canWrite) return <div className="page-container max-w-4xl"><p role="alert" className="alert-error text-sm">{t('architectures.managerOnly')}</p></div>
    return <NewArchitecture specification={specification} />
  }

  return (
    <div className="page-container max-w-4xl space-y-6">
      <Breadcrumbs items={[
        { label: t('specifications.title'), to: '/company/specifications' },
        { label: specification.title, to: specificationPath(specification.id) },
        { label: architecture.title },
      ]} />
      <div className="page-header">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="page-title">{architecture.title}</h1>
            <span className="text-sm text-gray-400">{t('specifications.version', { version: architecture.version })}</span>
            <StatusBadge status={architecture.status} />
          </div>
          <p className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
            {architecture.drafted_by && <span>{t('specifications.draftedBy', { who: architecture.drafted_by })}</span>}
            {architecture.approved_by && architecture.approved_at && (
              <span>{t('specifications.approvedBy', { who: architecture.approved_by, date: formatDate(architecture.approved_at) })}</span>
            )}
            {architecture.supersedes_id !== null && (
              <Link to={architecturePath(specification.id, architecture.supersedes_id)} className="text-brand-300 hover:underline">
                {t('specifications.supersedes', { id: architecture.supersedes_id })}
              </Link>
            )}
            <CheckSummary check={architecture.check} />
          </p>
          {architecture.written_path && architecture.written_at && (
            <p className="text-xs text-emerald-300">
              {t('architectures.writtenTo', { path: architecture.written_path, date: formatDate(architecture.written_at), hash: (architecture.written_sha256 ?? '').slice(0, 12) })}
            </p>
          )}
        </div>
      </div>

      {editing ? (
        <ArchitectureEditor
          initial={toBody(architecture)}
          requirements={specification.requirements}
          saving={update.isPending}
          error={update.isError ? errorDetail(update.error) : null}
          onCancel={() => { update.reset(); setEditing(false) }}
          onSave={body => update.mutate({ id: architecture.id, body }, { onSuccess: () => setEditing(false) })}
        />
      ) : (
        <>
          <SourcesConsulted sources={architecture.sources ?? []} headingId="architecture-sources" />
          {architecture.overview && (
            <section aria-labelledby="architecture-overview" className="card p-4">
              <h2 id="architecture-overview" className="mb-1 text-xs font-semibold uppercase tracking-wider text-gray-500">{t('architectures.overview')}</h2>
              <p className="text-sm text-gray-200 whitespace-pre-wrap">{architecture.overview}</p>
            </section>
          )}
          {canWrite && <Actions architecture={architecture} onEdit={() => setEditing(true)} />}
          <CheckPanel architecture={architecture} />
          <section aria-labelledby="architecture-questions" className="card p-4 space-y-2">
            <h2 id="architecture-questions" className="text-sm font-semibold text-white">{t('specifications.questions.title')}</h2>
            {architecture.questions.length === 0 ? (
              <p className="text-sm text-gray-500">{t('architectures.questions.none')}</p>
            ) : (
              <ul className="space-y-3">
                {architecture.questions.map((_, index) => (
                  <QuestionRow key={index} architecture={architecture} index={index} canAnswer={canWrite && architecture.status === 'draft'} />
                ))}
              </ul>
            )}
          </section>
          <Modules architecture={architecture} />
          <Interfaces architecture={architecture} />
          <Decisions architecture={architecture} />
          <section aria-labelledby="architecture-conventions" className="card p-4 space-y-2">
            <h2 id="architecture-conventions" className="text-sm font-semibold text-white">{t('architectures.conventions.title')}</h2>
            {architecture.conventions.length === 0 ? (
              <p className="text-sm text-gray-500">{t('architectures.conventions.none')}</p>
            ) : (
              <ul className="list-disc space-y-1 pl-5 text-sm text-gray-200">
                {architecture.conventions.map((convention, i) => <li key={i}>{convention}</li>)}
              </ul>
            )}
          </section>
          <MarkdownView architecture={architecture} />
        </>
      )}
    </div>
  )
}
