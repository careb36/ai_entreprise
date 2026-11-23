import { useState, type ReactNode } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeft, Ban, Circle, CircleX, CircleCheck, CirclePlay, ClipboardCheck, ClipboardList, Clock, Eye, Flag, Hourglass,
  MessageSquare, RefreshCw, RotateCcw, SearchX, ShieldCheck, User, type LucideIcon,
} from 'lucide-react'
import { Verdict } from '../components/Verdict'
import { Conversation } from '../components/Conversation'
import { useAppStore } from '../store/useAppStore'
import { useAgentRuns, useCapabilities, useCurrentRole, useSprintBoard } from '../hooks/useQueries'
import { SkeletonBlock, SkeletonLine } from '../components/Skeleton'
import { ApiError, type BacklogItem } from '../services/api'
import { parseRouteId } from '../lib/routeId'
import { carryOverLineage } from '../lib/carryOver'
import { TaskCostCard } from '../components/CostSummary'
import { TaskRelations } from '../components/TaskRelations'
import { RunSecretsPanel } from '../components/RunSecretsPanel'
import { TaskEventLog } from '../components/TaskEventLog'
import { TaskComments } from '../components/TaskComments'
import { TaskEditForm } from '../components/TaskEditForm'
import { TASK_WORKER_PRODUCER, taskExternalRef } from '../lib/taskEvidence'
import { CountPill, EmptyState, PageHeader, Panel, StatusBadge, type StatusTone } from '../components/ui'
import { commentAuthor } from '../lib/commentAuthor'
import { lineagePath } from './Lineage'

/** Each status and priority as an icon, a word and a tone: never the colour alone. */
const STATUS_LOOK: Record<BacklogItem['status'], { icon: LucideIcon; tone: StatusTone }> = {
  todo: { icon: Circle, tone: 'neutral' },
  in_progress: { icon: CirclePlay, tone: 'brand' },
  in_review: { icon: Eye, tone: 'warning' },
  done: { icon: CircleCheck, tone: 'success' },
  blocked: { icon: Ban, tone: 'danger' },
  discarded: { icon: CircleX, tone: 'neutral' },
}

const PRIORITY_TONES: Record<BacklogItem['priority'], StatusTone> = {
  critical: 'danger',
  high: 'warning',
  medium: 'info',
  low: 'neutral',
}

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}

/** A small heading inside a panel, for a group of rows (Observed, Reported). */
function GroupLabel({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <h3 className="text-meta mb-1.5 flex items-center gap-1 font-semibold uppercase tracking-wide">
      <Icon size={11} aria-hidden="true" /> {children}
    </h3>
  )
}

/**
 * The latest recorded delivery of this task. What the Verifier observed (the
 * verdict) and what the agent reported (the files it claims) are two groups
 * with their own heading, so a claim is never styled as an observation.
 */
function TaskEvidence({ companyId, taskId }: { companyId: number; taskId: number }) {
  const { t } = useTranslation()
  const { data, isLoading, isError, refetch } = useAgentRuns(companyId, {
    producer: TASK_WORKER_PRODUCER,
    external_ref: taskExternalRef(taskId),
    limit: 1,
  })
  const { data: capabilities } = useCapabilities()
  const run = data?.agent_runs[0]
  const files: unknown = run?.claims?.files
  const claimedFiles = Array.isArray(files)
    ? files.filter((file): file is string => typeof file === 'string')
    : []

  return (
    <Panel
      title={<span className="flex items-center gap-1.5"><ClipboardCheck size={13} aria-hidden="true" className="text-gray-400" />{t('taskDetails.evidence.title', { defaultValue: 'Evidence' })}</span>}
      actions={run && (
        <Link to={`/evidence/${run.id}`} className="btn-tool text-brand-300 hover:text-brand-200">
          {t('taskDetails.evidence.view', { defaultValue: 'View evidence' })}
        </Link>
      )}>
      <div className="p-3">
        {isLoading ? (
          <div role="status" aria-label={t('taskDetails.evidence.loading', { defaultValue: 'Loading evidence…' })}>
            <SkeletonBlock className="h-12 motion-reduce:animate-none" />
          </div>
        ) : isError ? (
          <div className="flex flex-wrap items-center gap-2">
            <p role="alert" className="text-xs text-red-300">
              {t('taskDetails.evidence.error', { defaultValue: 'Failed to load the evidence for this task.' })}
            </p>
            <button type="button" onClick={() => refetch()} className="btn-tool">
              <RefreshCw size={13} aria-hidden="true" />{t('common.retry')}
            </button>
          </div>
        ) : !run ? (
          <p className="text-xs text-gray-400">{t('taskDetails.evidence.empty', { defaultValue: 'No evidence recorded yet.' })}</p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <GroupLabel icon={ShieldCheck}>{t('taskDetails.evidence.observed')}</GroupLabel>
              <div className="flex flex-wrap items-center gap-2">
                <Verdict status={run.status} compact />
                <span className="text-meta tabular-nums">
                  {t('taskDetails.evidence.version', { version: run.version, defaultValue: 'Version {{version}}' })}
                </span>
              </div>
            </div>
            <div>
              <GroupLabel icon={MessageSquare}>{t('taskDetails.evidence.reported')}</GroupLabel>
              <p className="text-meta mb-1.5">
                {t('taskDetails.evidence.claimedFiles', { defaultValue: 'Files the agent claims to have written' })}
              </p>
              {claimedFiles.length > 0 ? (
                <ul className="flex flex-wrap gap-1">
                  {claimedFiles.map(file => (
                    <li key={file} className="chip max-w-full break-all border-dashed font-mono">
                      {capabilities?.memory_graph
                        ? <Link to={lineagePath(file)} className="rounded-sm hover:text-brand-300 hover:underline"
                            title={t('lineage.open', { defaultValue: 'See the decisions recorded on this file' })}>{file}</Link>
                        : file}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-gray-400">{t('taskDetails.evidence.noFiles', { defaultValue: 'No files claimed.' })}</p>
              )}
            </div>
          </div>
        )}
      </div>
    </Panel>
  )
}

/** Links to other tasks of the sprint, by id and, when the board has them, by title. */
function TaskLinks({ ids, sprintId, titles }: { ids: number[]; sprintId: number; titles: Map<number, string> }) {
  const { t } = useTranslation()
  return (
    <>
      {ids.map(id => (
        <Link key={id} to={`/sprints/${sprintId}/tasks/${id}`}
          className="chip text-brand-300 hover:bg-surface-800 hover:text-brand-200">
          {titles.has(id)
            ? t('taskDetails.taskWithTitle', { id, title: titles.get(id), defaultValue: 'task #{{id}}: {{title}}' })
            : t('taskDetails.taskNumber', { id, defaultValue: 'task #{{id}}' })}
        </Link>
      ))}
    </>
  )
}

export default function TaskDetails() {
  const { t } = useTranslation()
  const { taskId, sprintId: paramSprintId } = useParams<{ taskId: string; sprintId: string }>()
  const { activeSprintId, companyId } = useAppStore()
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  // Keep the lookup context visible if a 404 clears the persisted selection.
  const [lookup] = useState({ sprintId: activeSprintId, companyId })

  const parsedTaskId = parseRouteId(taskId)
  const resolvedSprintId = paramSprintId ? parseRouteId(paramSprintId)
    : activeSprintId ?? (companyId === lookup.companyId ? lookup.sprintId : null)
  const { data: board, isLoading, isError, error, refetch } = useSprintBoard(parsedTaskId === null ? null : resolvedSprintId)

  const task: BacklogItem | undefined = board
    ? Object.values(board.columns).flat().find(item => item.id === parsedTaskId)
    : undefined

  const sprintsLabel = t('nav.sprints', { defaultValue: 'Sprints' })
  const backTo = resolvedSprintId ? `/sprints/${resolvedSprintId}/board` : '/sprints'

  if (isLoading) {
    return (
      <div role="status" aria-label={t('common.loading', { defaultValue: 'Loading...' })} className="page-container max-w-3xl space-y-4">
        <SkeletonLine className="h-4 w-32 motion-reduce:animate-none" />
        <SkeletonBlock className="h-8 w-3/4 motion-reduce:animate-none" />
        <SkeletonBlock className="h-24 motion-reduce:animate-none" />
        <div className="grid grid-cols-2 gap-3">
          <SkeletonBlock className="h-12 motion-reduce:animate-none" />
          <SkeletonBlock className="h-12 motion-reduce:animate-none" />
        </div>
      </div>
    )
  }

  if (parsedTaskId === null || resolvedSprintId === null || isError || !task) {
    const sprintNotFound = error instanceof ApiError && error.status === 404
    const message = parsedTaskId === null ? 'taskDetails.invalidId'
      : paramSprintId && resolvedSprintId === null ? 'workspace.invalidId'
      : resolvedSprintId === null ? 'taskDetails.noSprint'
      : isError ? sprintNotFound ? 'taskDetails.sprintNotFound' : 'taskDetails.loadError'
      : 'taskDetails.notFound'
    return (
      <div className="page-container max-w-3xl">
        <EmptyState icon={SearchX} dashed
          title={<span role="alert">{t(message, { id: paramSprintId, taskId, sprintId: resolvedSprintId })}</span>}
          actions={<>
            {parsedTaskId !== null && resolvedSprintId !== null && isError && !sprintNotFound && (
              <button type="button" onClick={() => refetch()} className="btn-tool border border-surface-700">
                <RefreshCw size={13} aria-hidden="true" />{t('common.retry')}
              </button>
            )}
            <Link to={sprintNotFound ? '/sprints' : backTo} className="btn-tool border border-surface-700">
              <ArrowLeft size={13} aria-hidden="true" /> {t('common.back')}
            </Link>
          </>} />
      </div>
    )
  }

  const lineage = carryOverLineage(task.comments)
  // The acceptance criteria this task covers, when its sprint was planned from a specification.
  const specificationId = board?.sprint?.specification_id ?? null
  const covers = specificationId !== null && task.covers ? task.covers : []
  // The tasks of this sprint this one must come after: those not done yet, or all of them once released.
  const waitingFor = task.waiting_for ?? []
  const dependsOn = waitingFor.length > 0 ? waitingFor : task.depends_on ?? []
  const titles = new Map(Object.values(board?.columns ?? {}).flat().map(item => [item.id, item.title]))
  const statusLook = STATUS_LOOK[task.status] ?? { icon: Circle, tone: 'neutral' as StatusTone }

  return (
    <div className="page-container max-w-3xl space-y-4">
      <div className="space-y-2">
        <PageHeader
          breadcrumbs={[
            { label: sprintsLabel, to: '/sprints' },
            {
              label: board?.sprint?.sprint_name || t('workspace.sprintLabel', { id: resolvedSprintId, defaultValue: 'Sprint #{{id}}' }),
              to: backTo,
            },
            { label: task.title },
          ]}
          title={task.title}
          subtitle={
            <span className="flex flex-wrap items-center gap-1.5">
              <StatusBadge icon={statusLook.icon} tone={statusLook.tone} label={t(`kanban.status.${task.status}`, task.status)} />
              <StatusBadge icon={Flag} tone={PRIORITY_TONES[task.priority] ?? 'neutral'} label={t(`kanban.${task.priority}`, task.priority)} />
              {task.kind === 'acceptance_tests' && (
                <StatusBadge icon={ClipboardCheck} tone="brand" label={t('kanban.acceptanceTests')} />
              )}
            </span>
          }
        />
        {covers.length > 0 && specificationId !== null && (
          <p aria-label={t('specifications.taskCovers')} className="flex flex-wrap items-center gap-1.5 text-xs text-gray-400">
            <ClipboardList size={12} aria-hidden="true" />
            <span>{t('specifications.taskCovers')}</span>
            {covers.map(id => (
              <Link key={id} to={`/company/specifications/${specificationId}`}
                className="chip font-mono text-brand-300 hover:bg-surface-800 hover:text-brand-200">{id}</Link>
            ))}
          </p>
        )}
        {dependsOn.length > 0 && (
          <p aria-label={t(waitingFor.length > 0 ? 'taskDetails.waitsFor' : 'taskDetails.dependedOn')}
            className={`flex flex-wrap items-center gap-1.5 text-xs ${waitingFor.length > 0 ? 'text-amber-300' : 'text-gray-400'}`}>
            <Hourglass size={12} aria-hidden="true" />
            <span>{t(waitingFor.length > 0 ? 'taskDetails.waitsFor' : 'taskDetails.dependedOn')}</span>
            <TaskLinks ids={dependsOn} sprintId={resolvedSprintId} titles={titles} />
          </p>
        )}
      </div>

      {/* Carry-over lineage: another attempt at an earlier task, or the attempt this one got */}
      {(lineage.from || lineage.to) && (
        <nav aria-label={t('taskDetails.lineageLabel', { defaultValue: 'Earlier and later attempts' })} className="panel space-y-1 px-3 py-2.5 text-xs text-gray-300">
          {lineage.from && (
            <p>
              <RotateCcw size={11} className="mr-1 inline text-gray-400" aria-hidden="true" />
              {t('taskDetails.carriedFrom', { sprint: lineage.from.sprintName, defaultValue: 'Another attempt at a task blocked in {{sprint}}:' })}{' '}
              <Link to={`/task/${lineage.from.taskId}`} className="rounded-sm text-brand-300 hover:underline">
                {t('taskDetails.taskNumber', { id: lineage.from.taskId, defaultValue: 'task #{{id}}' })}
              </Link>
            </p>
          )}
          {lineage.to && (
            <p>
              <RotateCcw size={11} className="mr-1 inline text-gray-400" aria-hidden="true" />
              {t('taskDetails.carriedTo', { sprint: lineage.to.sprintName, defaultValue: 'Given another attempt in {{sprint}}:' })}{' '}
              <Link to={`/sprints/${lineage.to.sprintId}/tasks/${lineage.to.taskId}`} className="rounded-sm text-brand-300 hover:underline">
                {t('taskDetails.taskNumber', { id: lineage.to.taskId, defaultValue: 'task #{{id}}' })}
              </Link>
            </p>
          )}
        </nav>
      )}

      {/* Description and meta */}
      <Panel>
        {task.description && (
          <div className="border-b border-surface-700 px-3 py-3">
            <p className="text-meta mb-1.5 font-semibold uppercase tracking-wide">{t('generate.story')}</p>
            <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-gray-200">{task.description}</p>
          </div>
        )}
        {canManage && resolvedSprintId !== null && (
          <div className="border-b border-surface-700 px-3 py-3">
            <TaskEditForm key={`${task.id}:${task.title}:${task.description ?? ''}`} task={task} sprintId={resolvedSprintId} />
          </div>
        )}
        <dl className="grid grid-cols-2 divide-x divide-surface-700">
          <div className="px-3 py-2.5">
            <dt className="text-meta mb-0.5 flex items-center gap-1 font-semibold uppercase tracking-wide">
              <User size={11} aria-hidden="true" /> {t('kanban.assignee')}
            </dt>
            <dd className="text-[13px] text-gray-100">{task.assignee || t('common.na')}</dd>
          </div>
          <div className="px-3 py-2.5">
            <dt className="text-meta mb-0.5 flex items-center gap-1 font-semibold uppercase tracking-wide">
              <Clock size={11} aria-hidden="true" /> {t('support.updated')}
            </dt>
            <dd className="text-[13px] tabular-nums text-gray-100">{formatDate(task.updated_at)}</dd>
          </div>
        </dl>
      </Panel>

      <TaskComments task={task} />
      {companyId !== null && <TaskEvidence companyId={companyId} taskId={task.id} />}
      {companyId !== null && <TaskRelations taskId={task.id} />}
      {companyId !== null && <RunSecretsPanel companyId={companyId} taskId={task.id} />}
      {companyId !== null && <TaskCostCard taskId={task.id} />}
      {companyId !== null && <TaskEventLog taskId={task.id} />}

      {companyId !== null && <Conversation companyId={companyId} taskId={task.id} />}

      {/* Comments */}
      {task.comments.length > 0 && (
        <Panel
          title={<span className="flex items-center gap-1.5"><MessageSquare size={13} aria-hidden="true" className="text-gray-400" />{t('support.timeline')}</span>}
          count={<CountPill count={task.comments.length} label={t('taskDetails.commentCount', { count: task.comments.length })} />}>
          <ul className="data-list">
            {task.comments.map((c, i) => (
              <li key={i} className="data-row flex gap-3">
                <div aria-hidden="true" className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-brand-600/40 bg-brand-600/30 text-[10px] font-bold text-brand-300">
                  {commentAuthor(c, t).slice(0, 1).toUpperCase()}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="mb-0.5 flex flex-wrap items-center gap-2">
                    <span className="text-xs font-medium text-gray-200">{commentAuthor(c, t)}</span>
                    <span className="text-meta tabular-nums">{formatDate(c.created_at)}</span>
                  </div>
                  <p className="text-xs leading-relaxed text-gray-300">{c.text}</p>
                </div>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  )
}
