import { useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, CircleDashed, History, ShieldCheck, ShieldX, type LucideIcon } from 'lucide-react'
import { errorDetail } from '../services/api'
import { useTaskEvents, type ChainStatus, type TaskEvent } from '../services/auditTrail'
import { CountPill, LoadError, LoadingRows, Panel, StatusBadge, type StatusTone } from './ui'

const CHAIN_LOOK: Record<ChainStatus, { icon: LucideIcon; tone: StatusTone }> = {
  intact: { icon: ShieldCheck, tone: 'success' },
  broken: { icon: ShieldX, tone: 'danger' },
  empty: { icon: CircleDashed, tone: 'neutral' },
}

function formatWhen(value: string | null, locale: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'medium' }).format(date)
}

/**
 * A task's event log (`GET /v1/tasks/{id}/events`, Viewer): its events in
 * order with who recorded each, the state of the task's own hash chain as
 * the backend verified it, and which record the board reads. Read when
 * opened, since the backend walks the chain on each read.
 */
export function TaskEventLog({ taskId }: { taskId: number }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const regionId = useId()
  const log = useTaskEvents(taskId, open)
  const chain = log.data?.chain
  const look = chain ? CHAIN_LOOK[chain.status] ?? CHAIN_LOOK.empty : null

  return (
    <Panel
      title={<span className="flex items-center gap-1.5"><History size={13} aria-hidden="true" className="text-gray-400" />{t('taskEvents.title')}</span>}
      count={log.data ? <CountPill count={log.data.events.length} label={t('taskEvents.count', { count: log.data.events.length })} /> : undefined}
      actions={(
        <button type="button" className="btn-tool" aria-expanded={open} aria-controls={regionId} onClick={() => setOpen(value => !value)}>
          {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
          {open ? t('taskEvents.hide') : t('taskEvents.show')}
        </button>
      )}>
      <div id={regionId} hidden={!open}>
        {open && (log.isLoading ? (
          <div className="p-3"><LoadingRows rows={1} label={t('taskEvents.loading')} /></div>
        ) : log.isError ? (
          <div className="p-3"><LoadError onRetry={() => log.refetch()}>{t('taskEvents.loadError', { message: errorDetail(log.error) })}</LoadError></div>
        ) : log.data && chain && look ? (
          <>
            <div className="flex flex-wrap items-center gap-2 border-b border-surface-700 px-3 py-2 text-xs">
              <StatusBadge icon={look.icon} tone={look.tone} label={t(`taskEvents.chain.${chain.status}`)} />
              <span className="text-gray-400">{t(`taskEvents.source.${log.data.source}`)}</span>
            </div>
            {chain.breaks.length > 0 && (
              <ul role="alert" className="alert-error m-3 list-none flex-col gap-1 text-xs">
                {chain.breaks.map(b => (
                  <li key={`${b.seq}-${b.reason}`}>{t('taskEvents.break', { seq: b.seq, reason: t(`auditTrail.reasons.${b.reason}`, { defaultValue: b.reason }) })}</li>
                ))}
              </ul>
            )}
            {log.data.events.length === 0 ? (
              <p className="px-3 py-3 text-xs text-gray-400">{t('taskEvents.empty')}</p>
            ) : (
              <ol className="data-list" aria-label={t('taskEvents.listLabel')}>
                {log.data.events.map(event => <EventRow key={event.id} event={event} />)}
              </ol>
            )}
          </>
        ) : null)}
      </div>
    </Panel>
  )
}

function EventRow({ event }: { event: TaskEvent }) {
  const { t, i18n } = useTranslation()
  const hasPayload = Object.keys(event.payload).length > 0
  return (
    <li className="data-row space-y-1 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-8 text-right tabular-nums text-gray-400">{event.seq ?? '—'}</span>
        <code className="chip font-mono">{event.kind}</code>
        {event.actor && <span className="text-gray-300">{t('taskEvents.by', { actor: event.actor })}</span>}
        {event.task_status && <span className="text-gray-400">{t('taskEvents.status', { status: event.task_status })}</span>}
        {event.run_id !== null && <Link to={`/evidence/${event.run_id}`} className="text-brand-300 hover:underline">{t('taskEvents.run', { id: event.run_id })}</Link>}
        {event.legacy && <span className="text-meta">{t('taskEvents.legacy')}</span>}
        <time dateTime={event.created_at ?? undefined} className="ml-auto tabular-nums text-gray-400">{formatWhen(event.created_at, i18n.language)}</time>
      </div>
      {hasPayload && (
        <details className="pl-10">
          <summary className="cursor-pointer text-gray-400">{t('taskEvents.payload')}</summary>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-950 p-2 font-mono text-[11px] text-gray-300">
            {JSON.stringify(event.payload, null, 2)}
          </pre>
        </details>
      )}
    </li>
  )
}
