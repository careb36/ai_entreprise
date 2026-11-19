import { useId, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, ScrollText } from 'lucide-react'
import { errorDetail } from '../services/api'
import { useSprintEvents, type SprintEvent } from '../services/sprintEvents'
import { CountPill, LoadError, LoadingRows, Panel, Toolbar } from './ui'

const WHEN: Intl.DateTimeFormatOptions = { dateStyle: 'short', timeStyle: 'medium' }

/**
 * The sprint's raw event list (`GET /v1/sprints/{id}/events`, Viewer): every
 * event in order with its task, type and time, a type filter, and each
 * payload as text on request. Read when opened. It shows the record as
 * stored; a task's own page has its event log with the chain's state.
 */
export function SprintEventLog({ sprintId }: { sprintId: number }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [type, setType] = useState('')
  const ids = { region: useId(), type: useId() }
  const query = useSprintEvents(sprintId, open)
  const events = query.data?.events ?? []
  const types = [...new Set(events.map(e => e.event_type))].sort()
  const shown = type ? events.filter(e => e.event_type === type) : events

  return (
    <Panel headingLevel={3}
      title={<span className="flex items-center gap-1.5"><ScrollText size={13} aria-hidden="true" className="text-gray-400" />{t('sprintEvents.title')}</span>}
      count={query.data ? <CountPill count={shown.length} total={events.length} label={t('sprintEvents.count', { count: shown.length })} /> : undefined}
      actions={(
        <button type="button" className="btn-tool" aria-expanded={open} aria-controls={ids.region} onClick={() => setOpen(value => !value)}>
          {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
          {open ? t('sprintEvents.hide') : t('sprintEvents.show')}
        </button>
      )}>
      <div id={ids.region} hidden={!open}>
        {open && (query.isLoading ? (
          <div className="p-3"><LoadingRows rows={1} label={t('sprintEvents.loading')} /></div>
        ) : query.isError ? (
          <div className="p-3"><LoadError onRetry={() => query.refetch()}>{t('sprintEvents.loadError', { message: errorDetail(query.error) })}</LoadError></div>
        ) : events.length === 0 ? (
          <p className="px-3 py-3 text-xs text-gray-400">{t('sprintEvents.empty')}</p>
        ) : (
          <>
            <div className="border-b border-surface-700 px-3 py-2">
              <Toolbar label={t('sprintEvents.filters')}>
                <label htmlFor={ids.type} className="text-xs text-gray-300">{t('sprintEvents.type')}</label>
                <select id={ids.type} value={type} onChange={event => setType(event.target.value)} className="select control-sm">
                  <option value="">{t('sprintEvents.allTypes')}</option>
                  {types.map(value => <option key={value} value={value}>{value}</option>)}
                </select>
              </Toolbar>
            </div>
            <ol className="data-list max-h-96 overflow-y-auto" aria-label={t('sprintEvents.listLabel')}>
              {shown.map(event => <EventRow key={event.id} event={event} sprintId={sprintId} />)}
            </ol>
          </>
        ))}
      </div>
    </Panel>
  )
}

function EventRow({ event, sprintId }: { event: SprintEvent; sprintId: number }) {
  const { t, i18n } = useTranslation()
  const payload = event.payload && Object.keys(event.payload).length > 0 ? event.payload : null
  return (
    <li className="data-row space-y-1 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <code className="chip font-mono">{event.event_type}</code>
        {event.task_id !== null && (
          <Link to={`/sprints/${sprintId}/tasks/${event.task_id}`} className="text-brand-300 hover:underline">{t('taskRelations.task', { id: event.task_id })}</Link>
        )}
        <time dateTime={event.created_at ?? undefined} className="ml-auto tabular-nums text-gray-400">
          {event.created_at ? new Intl.DateTimeFormat(i18n.language, WHEN).format(new Date(event.created_at)) : '—'}
        </time>
      </div>
      {payload && (
        <details>
          <summary className="cursor-pointer text-gray-400">{t('sprintEvents.payload')}</summary>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-950 p-2 font-mono text-[11px] text-gray-300">
            {JSON.stringify(payload, null, 2)}
          </pre>
        </details>
      )}
    </li>
  )
}
