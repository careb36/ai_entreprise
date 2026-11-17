import { useTranslation } from 'react-i18next'
import { CheckCircle2, CircleDot, HelpCircle, Loader2, RotateCcw, XCircle } from 'lucide-react'
import type { FindingOutcome, FindingReason, FindingStatus } from '../services/api'

/**
 * The parts the findings list and a finding's page share: its status, always
 * as text with an icon beside it (never colour alone), and the table of the
 * cycles run for it, each with the fitness decision, its reasons, the
 * baseline's (B) and the candidate's (C) sha and the date. Everything shown is
 * what the platform recorded; nothing here writes.
 */
const STATUS_STYLE: Record<FindingStatus, { tone: string; icon: typeof CircleDot }> = {
  open: { tone: 'bg-sky-900/40 text-sky-200', icon: CircleDot },
  in_cycle: { tone: 'bg-brand-900/40 text-brand-200', icon: Loader2 },
  kept: { tone: 'bg-emerald-900/40 text-emerald-200', icon: CheckCircle2 },
  reverted: { tone: 'bg-red-900/40 text-red-200', icon: RotateCcw },
  unmeasured: { tone: 'bg-amber-900/40 text-amber-200', icon: HelpCircle },
  closed_by_person: { tone: 'bg-surface-700 text-gray-300', icon: XCircle },
}

const DECISION_TONE: Record<string, string> = {
  keep: 'text-emerald-300',
  revert: 'text-red-300',
  unmeasured: 'text-amber-300',
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
  const Icon = style.icon
  return (
    <span className={`badge inline-flex items-center gap-1 text-xs ${style.tone}`}>
      <Icon size={12} aria-hidden="true" />
      {t(`findings.status.${status}`, { defaultValue: status })}
    </span>
  )
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
  if (outcomes.length === 0) return <p className="text-xs text-gray-400">{t('findings.outcomes.none')}</p>
  return (
    <div className="overflow-x-auto">
      <table aria-label={label} className="w-full text-left text-xs">
        <thead className="text-gray-400">
          <tr>
            <th scope="col" className="py-1 pr-3 font-medium">{t('findings.outcomes.cycle')}</th>
            <th scope="col" className="py-1 pr-3 font-medium">{t('findings.outcomes.decision')}</th>
            <th scope="col" className="py-1 pr-3 font-medium">{t('findings.outcomes.reasons')}</th>
            <th scope="col" className="py-1 pr-3 font-medium">{t('findings.outcomes.baseline')}</th>
            <th scope="col" className="py-1 pr-3 font-medium">{t('findings.outcomes.candidate')}</th>
            <th scope="col" className="py-1 font-medium">{t('findings.outcomes.date')}</th>
          </tr>
        </thead>
        <tbody className="align-top text-gray-300">
          {outcomes.map((outcome, index) => (
            <tr key={`${outcome.candidate_sha}-${index}`} className="border-t border-surface-700/60">
              <td className="py-1.5 pr-3">{outcome.cycle ?? index + 1}</td>
              <td className={`py-1.5 pr-3 font-medium ${DECISION_TONE[outcome.decision] ?? ''}`}>
                {t(`findings.decision.${outcome.decision}`, { defaultValue: outcome.decision })}
              </td>
              <td className="py-1.5 pr-3">
                {outcome.reasons.length === 0
                  ? <span className="text-gray-500">{t('findings.outcomes.noReasons')}</span>
                  : <ul className="space-y-0.5">{outcome.reasons.map((reason, i) => <Reason key={i} reason={reason} />)}</ul>}
              </td>
              <td className="py-1.5 pr-3"><Sha value={outcome.baseline_sha} /></td>
              <td className="py-1.5 pr-3"><Sha value={outcome.candidate_sha} /></td>
              <td className="py-1.5 whitespace-nowrap">{formatFindingDate(outcome.date)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
