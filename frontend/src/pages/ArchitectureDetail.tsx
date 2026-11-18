/**
 * One architecture of a specification: its check (what blocks an approval,
 * what only warns), the Architect's open questions a Manager answers, the
 * modules with what they depend on and cover, the interfaces with their
 * contracts, the decisions and the conventions. A Manager edits a draft,
 * asks the Architect to revise it or approves it; an approved one gets a new
 * draft version with Revise, and is written to the repository as
 * `docs/ARCHITECTURE.md` through the workspace daemon. The `new` route
 * renders the editor for an architecture a person writes. The architecture
 * is the company's: the page names the version, whether it is the current
 * one, and lists every version of it (`ArchitectureHistory`).
 */
import { useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Check, ChevronDown, ChevronRight, CheckCircle, FileText, FolderOutput, Pencil, Send, Sparkles } from 'lucide-react'
import { ArchitectureEditor } from '../components/ArchitectureEditor'
import { ArchitectureHistory, ArchitectureVersionLine } from '../components/ArchitectureHistory'
import { FindingList, Where } from '../components/Findings'
import { LoadErrorAlert, LoadingLine, READABLE_TEXT, Spinner } from '../components/knowledge/PageStates'
import { SourcesConsulted } from '../components/SourcesConsulted'
import { WorkspaceDaemonHelp } from '../components/WorkspaceDaemonHelp'
import { CountPill, PageHeader, Panel } from '../components/ui'
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
  return <span className="inline-flex items-center gap-1"><Where where={id} />{name && <span className="text-xs text-gray-300">{name}</span>}</span>
}

/** A labelled line of chips under a module or an interface ("Depends on", "Covers"). */
function ChipLine({ label, children }: { label: string; children: ReactNode }) {
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
      <span className="text-meta font-medium uppercase tracking-wider">{label}</span>
      {children}
    </p>
  )
}

/** A section of the architecture: a titled panel with its count and its empty line. */
function Section({ title, count, countLabel, empty, children }: {
  title: string; count: number; countLabel: string; empty: string; children: ReactNode
}) {
  return (
    <Panel title={title} count={count > 0 ? <CountPill count={count} label={countLabel} /> : undefined}>
      {count === 0 ? <p className="px-3 py-2.5 text-[13px] text-gray-400">{empty}</p> : children}
    </Panel>
  )
}

function CheckPanel({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  const { check } = architecture
  return (
    <Panel title={t('specifications.check.title')}>
      <div className="space-y-3 p-3">
        {check.ok && (
          <p className="flex items-center gap-2 text-[13px] text-emerald-300">
            <CheckCircle size={14} aria-hidden="true" /> {t('architectures.check.ok')}
          </p>
        )}
        <FindingList findings={check.findings} severity="blocking" />
        <FindingList findings={check.findings} severity="warning" />
      </div>
    </Panel>
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

/** The title of an earlier specification the version covers, else its number. */
function earlierTitle(architecture: Architecture, specId: string): string {
  return architecture.earlier_specifications?.find(s => String(s.id) === specId)?.title ?? `#${specId}`
}

function Modules({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  const count = architecture.modules.length
  return (
    <Section title={t('architectures.modules.title')} count={count} countLabel={t('architectures.modules.countLabel', { count })}
      empty={t('architectures.modules.none')}>
      <ul className="data-list">
        {architecture.modules.map(module => (
          <li key={module.id} className="data-row space-y-2 py-3" aria-label={module.id}>
            <div className="flex flex-wrap items-center gap-2">
              <Where where={module.id} />
              <h3 className="text-[13px] font-semibold text-gray-100">{module.name}</h3>
              <code className="chip font-mono">{module.path}</code>
            </div>
            {module.responsibility && <p className={READABLE_TEXT}>{module.responsibility}</p>}
            {module.depends_on.length > 0 && (
              <ChipLine label={t('architectures.modules.dependsOn')}>
                {module.depends_on.map(id => <ModuleChip key={id} id={id} architecture={architecture} />)}
              </ChipLine>
            )}
            {module.covers.length > 0 && (
              <ChipLine label={t('architectures.modules.covers')}>
                {module.covers.map(id => (
                  <Link key={id} to={specificationPath(architecture.specification_id)} className="rounded-md hover:underline"><Where where={id} /></Link>
                ))}
              </ChipLine>
            )}
            {Object.entries(module.earlier_covers ?? {}).map(([specId, ids]) => (
              <ChipLine key={specId} label={t('architectures.modules.coversIn', { title: earlierTitle(architecture, specId) })}>
                {ids.map(id => (
                  <Link key={id} to={specificationPath(Number(specId))} className="rounded-md hover:underline"><Where where={id} /></Link>
                ))}
              </ChipLine>
            ))}
          </li>
        ))}
      </ul>
    </Section>
  )
}

function Interfaces({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  const count = architecture.interfaces.length
  return (
    <Section title={t('architectures.interfaces.title')} count={count} countLabel={t('architectures.interfaces.countLabel', { count })}
      empty={t('architectures.interfaces.none')}>
      <ul className="data-list">
        {architecture.interfaces.map(iface => (
          <li key={iface.id} className="data-row space-y-2 py-3" aria-label={iface.id}>
            <div className="flex flex-wrap items-center gap-2">
              <Where where={iface.id} />
              <h3 className="text-[13px] font-semibold text-gray-100">{iface.name}</h3>
              <span className="chip">{t(`architectures.kinds.${iface.kind}`, { defaultValue: iface.kind })}</span>
            </div>
            <ChipLine label={t('architectures.interfaces.providedBy')}>
              <ModuleChip id={iface.module_id} architecture={architecture} />
            </ChipLine>
            {iface.consumers.length > 0 && (
              <ChipLine label={t('architectures.interfaces.consumers')}>
                {iface.consumers.map(id => <ModuleChip key={id} id={id} architecture={architecture} />)}
              </ChipLine>
            )}
            {iface.contract && (
              <pre className="overflow-x-auto whitespace-pre-wrap rounded-md border border-surface-700 bg-surface-950/60 p-2.5 font-mono text-xs leading-relaxed text-gray-200">{iface.contract}</pre>
            )}
          </li>
        ))}
      </ul>
    </Section>
  )
}

function Decisions({ architecture }: { architecture: Architecture }) {
  const { t } = useTranslation()
  const count = architecture.decisions.length
  return (
    <Section title={t('architectures.decisions.title')} count={count} countLabel={t('architectures.decisions.countLabel', { count })}
      empty={t('architectures.decisions.none')}>
      <ul className="data-list">
        {architecture.decisions.map(decision => (
          <li key={decision.id} className="data-row space-y-2 py-3" aria-label={decision.id}>
            <div className="flex flex-wrap items-center gap-2">
              <Where where={decision.id} />
              <h3 className="text-[13px] font-semibold text-gray-100">{decision.title}</h3>
            </div>
            <dl className="space-y-2">
              {DECISION_FIELDS.map(field => decision[field] && (
                <div key={field}>
                  <dt className="text-meta font-medium uppercase tracking-wider">{t(`architectures.decisions.${field}`, { defaultValue: field })}</dt>
                  <dd className={`${READABLE_TEXT} mt-0.5 text-gray-300`}>{decision[field]}</dd>
                </div>
              ))}
            </dl>
          </li>
        ))}
      </ul>
    </Section>
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
      <button type="button" onClick={show} aria-expanded={open} className={`btn-tool ${open ? 'btn-tool-on' : ''}`}>
        {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
        <FileText size={13} aria-hidden="true" /> {t('architectures.markdown.view')}
      </button>
      {open && loading && <LoadingLine>{t('architectures.markdown.loading')}</LoadingLine>}
      {open && error && <p role="alert" className="text-[13px] text-red-300">{t('architectures.markdown.error', { message: error })}</p>}
      {open && text !== null && (
        <pre className="panel overflow-x-auto whitespace-pre-wrap p-4 font-mono text-xs leading-relaxed text-gray-200">{text}</pre>
      )}
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
      <section aria-label={t('specifications.actions.title')} className="panel space-y-3 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => write.mutate(architecture.id)} disabled={write.isPending} className="btn btn-primary btn-sm">
            {write.isPending ? <Spinner /> : <FolderOutput size={14} aria-hidden="true" />}
            {write.isPending ? t('architectures.actions.writing') : t('architectures.actions.write')}
          </button>
          <button type="button" onClick={reviseApproved} disabled={revise.isPending} className="btn-tool">
            {revise.isPending ? <Spinner /> : <Pencil size={13} aria-hidden="true" />}
            {t('specifications.actions.revise')}
          </button>
          <span className="text-xs text-gray-400">{t('architectures.actions.reviseHint')}</span>
          {revise.isError && !(revise.error instanceof ArchitectureConflict && revise.error.architectureId !== null) && (
            <span role="alert" className="text-[13px] text-red-300">{t('specifications.actions.reviseError', { message: errorDetail(revise.error) })}</span>
          )}
        </div>
        {write.isSuccess && write.data.written_path && (
          <p role="status" className="flex items-center gap-2 text-[13px] text-emerald-300">
            <CheckCircle size={14} aria-hidden="true" />
            {t('architectures.actions.written', { path: write.data.written_path, hash: (write.data.written_sha256 ?? '').slice(0, 12) })}
          </p>
        )}
        {write.isError && (daemonDown
          ? <WorkspaceDaemonHelp detail={daemonDown.detail} />
          : <p role="alert" className="text-[13px] text-red-300">{t('architectures.actions.writeError', { message: errorDetail(write.error) })}</p>)}
      </section>
    )
  }
  return (
    <section aria-label={t('specifications.actions.title')} className="panel space-y-3 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={onEdit} className="btn-tool">
          <Pencil size={13} aria-hidden="true" /> {t('specifications.actions.edit')}
        </button>
        <button type="button" disabled={redraft.isPending} className="btn-tool"
          onClick={() => redraft.mutate({ specification_id: specId, architecture_id: architecture.id })}>
          {redraft.isPending ? <Spinner /> : <Sparkles size={13} aria-hidden="true" />}
          {redraft.isPending ? t('architectures.drafting') : t('architectures.actions.reviseArchitect')}
        </button>
        <button type="button" onClick={() => approve.mutate(architecture.id)} disabled={!architecture.check.ok || approve.isPending}
          title={architecture.check.ok ? undefined : t('specifications.actions.approveBlocked')} className="btn btn-primary btn-sm">
          {approve.isPending ? <Spinner /> : <Check size={14} aria-hidden="true" />}
          {t('specifications.actions.approve')}
        </button>
        {!architecture.check.ok && <span className="text-xs text-gray-400">{t('specifications.actions.approveBlocked')}</span>}
        {approve.isError && (
          <span role="alert" className="text-[13px] text-red-300">{t('specifications.actions.approveError', { message: errorDetail(approve.error) })}</span>
        )}
        {redraft.isError && <span role="alert" className="text-[13px] text-red-300">{t('architectures.draftError', { message: errorDetail(redraft.error) })}</span>}
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
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader
        breadcrumbs={[
          { label: t('specifications.title'), to: '/company/specifications' },
          { label: specification.title, to: specificationPath(specification.id) },
          { label: t('architectures.editor.newTitle') },
        ]}
        title={t('architectures.editor.newTitle')}
      />
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
    return <div className="page-container max-w-4xl"><p role="alert" className="alert-error">{t('architectures.invalidId', { id: id ?? specId })}</p></div>
  }
  if (specQuery.isLoading || (!isNew && query.isLoading)) {
    return <div className="page-container max-w-4xl"><LoadingLine>{t('architectures.loadingOne')}</LoadingLine></div>
  }
  const failed = specQuery.isError ? specQuery : query.isError ? query : null
  if (failed || !specification || (!isNew && !architecture)) {
    return (
      <div className="page-container max-w-4xl space-y-3">
        <LoadErrorAlert onRetry={() => { specQuery.refetch(); query.refetch() }}>
          {t('architectures.loadOneError', { message: failed?.error ? errorDetail(failed.error) : '' })}
        </LoadErrorAlert>
        <Link to={specificationPath(specificationId)} className="btn-tool">{t('architectures.back')}</Link>
      </div>
    )
  }
  if (isNew || !architecture) {
    if (!canWrite) return <div className="page-container max-w-4xl"><p role="alert" className="alert-error">{t('architectures.managerOnly')}</p></div>
    return <NewArchitecture specification={specification} />
  }

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader
        breadcrumbs={[
          { label: t('specifications.title'), to: '/company/specifications' },
          { label: specification.title, to: specificationPath(specification.id) },
          { label: architecture.title },
        ]}
        title={architecture.title}
        subtitle={(
          <span className="block space-y-1.5">
            <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs">
              <span className="tabular-nums text-gray-300">{t('specifications.version', { version: architecture.version })}</span>
              <StatusBadge status={architecture.status} />
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
            </span>
            <ArchitectureVersionLine architecture={architecture} />
            {architecture.written_path && architecture.written_at && (
              <span className="flex items-center gap-1.5 text-xs text-emerald-300">
                <FolderOutput size={12} aria-hidden="true" />
                {t('architectures.writtenTo', { path: architecture.written_path, date: formatDate(architecture.written_at), hash: (architecture.written_sha256 ?? '').slice(0, 12) })}
              </span>
            )}
          </span>
        )}
      />

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
            <Panel title={t('architectures.overview')}>
              <p className={`${READABLE_TEXT} p-3`}>{architecture.overview}</p>
            </Panel>
          )}
          {canWrite && <Actions architecture={architecture} onEdit={() => setEditing(true)} />}
          <CheckPanel architecture={architecture} />
          <Panel title={t('specifications.questions.title')}
            count={architecture.questions.length > 0
              ? <CountPill count={architecture.questions.length} label={t('specifications.questions.countLabel', { count: architecture.questions.length })} />
              : undefined}>
            {architecture.questions.length === 0 ? (
              <p className="px-3 py-2.5 text-[13px] text-gray-400">{t('architectures.questions.none')}</p>
            ) : (
              <ul className="data-list">
                {architecture.questions.map((_, index) => (
                  <QuestionRow key={index} architecture={architecture} index={index} canAnswer={canWrite && architecture.status === 'draft'} />
                ))}
              </ul>
            )}
          </Panel>
          <Modules architecture={architecture} />
          <Interfaces architecture={architecture} />
          <Decisions architecture={architecture} />
          <Panel title={t('architectures.conventions.title')}>
            {architecture.conventions.length === 0 ? (
              <p className="px-3 py-2.5 text-[13px] text-gray-400">{t('architectures.conventions.none')}</p>
            ) : (
              <ul className="max-w-prose list-disc space-y-1 py-2.5 pl-8 pr-3 text-[13px] leading-relaxed text-gray-200">
                {architecture.conventions.map((convention, i) => <li key={i}>{convention}</li>)}
              </ul>
            )}
          </Panel>
          <ArchitectureHistory architecture={architecture} />
          <MarkdownView architecture={architecture} />
        </>
      )}
    </div>
  )
}
