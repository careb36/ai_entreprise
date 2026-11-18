import { useTranslation } from 'react-i18next'
import { Check, CheckCircle2, CircleDot, HelpCircle, RefreshCw, RotateCcw, XCircle, type LucideIcon } from 'lucide-react'
import type { FindingOutcome, FindingReason, FindingStatus } from '../services/api'
import { StatusBadge, type StatusTone } from './ui'

/**
 * The parts the findings list and a finding's page share: its status, always
 * as text with an icon beside it (never colour alone), and the table of the
 * cycles run for it, each with the fitness decision, its reasons, the
 * baseline's (B) and the candidate's (C) sha and the date. Everything shown is
 * what the platform recorded; nothing here writes.
 */
const STATUS_STYLE: Record<FindingStatus, { tone: StatusTone; icon: LucideIcon }> = {
  open: { tone: 'info', icon: CircleDot },
  in_cycle: { tone: 'brand', icon: RefreshCw },
  kept: { tone: 'success', icon: CheckCircle2 },
  reverted: { tone: 'danger', icon: RotateCcw },
  unmeasured: { tone: 'warning', icon: HelpCircle },
  closed_by_person: { tone: 'neutral', icon: XCircle },
}

/** A cycle's decision, as an icon and a word like a status. */
const DECISION_LOOK: Record<string, { tone: string; icon: LucideIcon }> = {
  keep: { tone: 'text-emerald-300', icon: Check },
  revert: { tone: 'text-red-300', icon: RotateCcw },
  unmeasured: { tone: 'text-amber-300', icon: HelpCircle },
}

export function formatFindingDate(iso: string | null) {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function FindingStatusBadge({ status }: { status: FindingStatus }) {
  const { t } = useTranslation()
  const style = STATUS_STYLE[status] ?? STATUS_STYLE.open
  return <StatusBadge icon={style.icon} tone={style.tone} label={t(`findings.status.${status}`, { defaultValue: status })} />
}

function Sha({ value }: { value: string }) {
  return <code title={value} className="font-mono text-[11px] text-gray-200">{value.slice(0, 12)}</code>
}

function Reason({ reason }: { reason: FindingReason | string }) {
  if (typeof reason === 'string') return <li>{reason}</li>
  return (
    <li>
      {reason.guard && <code className="text-gray-200">{reason.guard}</code>} {reason.outcome}
      {reason.detail && <> — {reason.detail}</>}
    </li>
  )
}

export function FindingOutcomes({ outcomes, label }: { outcomes: FindingOutcome[]; label: string }) {
  const { t } = useTranslation()
  if (outcomes.length === 0) return <p className="text-[13px] text-gray-400">{t('findings.outcomes.none')}</p>
  return (
    <div className="overflow-x-auto">
      <table aria-label={label} className="w-full text-left text-xs">
        <thead className="text-[11px] uppercase tracking-wider text-gray-400">
          <tr>
            <th scope="col" className="py-1.5 pr-3 font-medium">{t('findings.outcomes.cycle')}</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">{t('findings.outcomes.decision')}</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">{t('findings.outcomes.reasons')}</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">{t('findings.outcomes.baseline')}</th>
            <th scope="col" className="py-1.5 pr-3 font-medium">{t('findings.outcomes.candidate')}</th>
            <th scope="col" className="py-1.5 font-medium">{t('findings.outcomes.date')}</th>
          </tr>
        </thead>
        <tbody className="align-top text-gray-300">
          {outcomes.map((outcome, index) => {
            const look = DECISION_LOOK[outcome.decision]
            const DecisionIcon = look?.icon
            return (
              <tr key={`${outcome.candidate_sha}-${index}`} className="border-t border-surface-700">
                <td className="py-2 pr-3 tabular-nums">{outcome.cycle ?? index + 1}</td>
                <td className={`py-2 pr-3 font-medium ${look?.tone ?? ''}`}>
                  <span className="inline-flex items-center gap-1 whitespace-nowrap">
                    {DecisionIcon && <DecisionIcon size={12} aria-hidden="true" />}
                    {t(`findings.decision.${outcome.decision}`, { defaultValue: outcome.decision })}
                  </span>
                </td>
                <td className="py-2 pr-3">
                  {outcome.reasons.length === 0
                    ? <span className="text-gray-400">{t('findings.outcomes.noReasons')}</span>
                    : <ul className="space-y-0.5">{outcome.reasons.map((reason, i) => <Reason key={i} reason={reason} />)}</ul>}
                </td>
                <td className="py-2 pr-3"><Sha value={outcome.baseline_sha} /></td>
                <td className="py-2 pr-3"><Sha value={outcome.candidate_sha} /></td>
                <td className="whitespace-nowrap py-2 tabular-nums">{formatFindingDate(outcome.date)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
