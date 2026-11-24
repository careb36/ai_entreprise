import { useTranslation } from 'react-i18next'
import { Clock, Hourglass, Lock, RefreshCw, ShieldAlert, Snowflake, UserRound } from 'lucide-react'
import type { TaskHold } from '../../services/api'

/*
 * What holds a task back, as its record says (`observed.hold`, backend
 * `services/tasks/task_hold.py`): a person, a dependency, a retry or a review
 * the worker runs on its own, a freeze, a risk hold, or the run window. The
 * word and an icon, never the colour alone.
 */

const LOOK: Record<TaskHold['kind'], { icon: typeof Lock; tone: string }> = {
  person: { icon: UserRound, tone: 'bg-red-950/70 text-red-200 ring-red-700/60' },
  dependency: { icon: Hourglass, tone: 'bg-amber-900/30 text-amber-300 ring-amber-700/40' },
  retry_pending: { icon: RefreshCw, tone: 'bg-sky-950/70 text-sky-200 ring-sky-700/60' },
  review_pending: { icon: RefreshCw, tone: 'bg-sky-950/70 text-sky-200 ring-sky-700/60' },
  frozen: { icon: Snowflake, tone: 'bg-slate-800 text-slate-200 ring-slate-600/60' },
  risk_hold: { icon: ShieldAlert, tone: 'bg-orange-950/70 text-orange-200 ring-orange-700/60' },
  run_window: { icon: Clock, tone: 'bg-indigo-950/70 text-indigo-200 ring-indigo-700/60' },
}

/** The hold's words: "Waits for #30" for a dependency, else its kind. */
export function holdLabel(t: ReturnType<typeof useTranslation>['t'], hold: TaskHold): string {
  if (hold.kind === 'dependency') {
    const ids = (hold.waiting_for ?? []).map(id => `#${id}`).join(', ')
    return ids ? t('taskHold.dependencyOn', { ids }) : t('taskHold.dependency')
  }
  return t(`taskHold.${hold.kind}`)
}

export function HoldBadge({ hold }: { hold?: TaskHold | null }) {
  const { t } = useTranslation()
  if (!hold || !LOOK[hold.kind]) return null
  const { icon: Icon, tone } = LOOK[hold.kind]
  const label = holdLabel(t, hold)
  const hint = t(`taskHold.hint.${hold.kind}`)
  const title = hold.reason ? `${hint} — ${hold.reason}` : hint
  return (
    <span data-hold={hold.kind} title={title}
      className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${tone}`}>
      <Icon size={11} aria-hidden="true" />{label}
      <span className="sr-only">{` — ${hint}`}</span>
    </span>
  )
}
