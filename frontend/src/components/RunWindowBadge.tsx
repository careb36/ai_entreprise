import { useTranslation } from 'react-i18next'
import { Clock } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { useSetSprintUrgent } from '../hooks/useRunWindow'
import { errorDetail } from '../services/api'
import type { SprintState } from '../services/workControl'

/** "Wed 01:00" in the viewer's own time zone and language. */
function localTime(iso: string, language: string): string {
  try {
    return new Date(iso).toLocaleString(language, { weekday: 'short', hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}

/**
 * The run window on the board (issue #736): while the company's window holds
 * the sprint's work back, when it starts, in the viewer's local time; and the
 * "urgent" toggle with which a Manager bypasses the window (audited). A
 * Viewer sees the hold and whether the sprint is urgent, never the toggle.
 */
export default function RunWindowBadge({ sprint }: { sprint: SprintState }) {
  const { t, i18n } = useTranslation()
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const setUrgent = useSetSprintUrgent(sprint.id)
  const hold = sprint.run_window
  const finished = sprint.status === 'completed' || sprint.status === 'cancelled'
  if (!hold?.waiting && !sprint.urgent && (!canManage || finished)) return null
  return (
    <div className="flex flex-wrap items-center gap-3 text-xs">
      {hold?.waiting && (
        <span role="status" className="inline-flex items-center gap-1.5 rounded-full border border-sky-500/40 bg-sky-500/10 px-2 py-0.5 text-sky-200"
          title={t('runWindow.waitingTitle', { time: hold.until_local, zone: hold.timezone })}>
          <Clock size={12} aria-hidden="true" />
          {t('runWindow.waiting', { time: localTime(hold.until, i18n.language) })}
        </span>
      )}
      {canManage && !finished ? (
        <label className="flex items-center gap-1.5 text-gray-300" title={t('runWindow.urgentHint')}>
          <input type="checkbox" checked={Boolean(sprint.urgent)} disabled={setUrgent.isPending}
            onChange={event => setUrgent.mutate(event.target.checked)} />
          {t('runWindow.urgent')}
        </label>
      ) : sprint.urgent ? (
        <span className="text-amber-300">{t('runWindow.urgentOn')}</span>
      ) : null}
      {setUrgent.isError && <span role="alert" className="text-red-300">{errorDetail(setUrgent.error)}</span>}
    </div>
  )
}
