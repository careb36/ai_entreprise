import { useTranslation } from 'react-i18next'
import { useSodReport } from '../hooks/useDuties'
import { Bot, CircleHelp, ShieldAlert, ShieldCheck, ShieldX, TriangleAlert, type LucideIcon } from 'lucide-react'
import { errorDetail, type SodApproval, type SodReport } from '../services/api'
import { Panel, StatusBadge, type StatusTone, TABLE, TD, TH, TR } from './ui'

const STATUSES = ['separated', 'exception', 'violation', 'unattributed', 'not_a_person'] as const

/** Each status as an icon, a word and a tone: never the colour alone. */
const STATUS_LOOK: Record<string, { icon: LucideIcon; tone: StatusTone }> = {
  separated: { icon: ShieldCheck, tone: 'success' },
  exception: { icon: ShieldAlert, tone: 'warning' },
  violation: { icon: ShieldX, tone: 'danger' },
  unattributed: { icon: CircleHelp, tone: 'neutral' },
  not_a_person: { icon: Bot, tone: 'neutral' },
}
const UNKNOWN_LOOK: { icon: LucideIcon; tone: StatusTone } = { icon: CircleHelp, tone: 'neutral' }

/**
 * The segregation-of-duties report (issue #543): every approval beside who
 * asked for the work, its status (an exception is the audited single-person
 * one), the refusals, and the matrix of who did which duty — all computed by
 * the backend from the records, never claimed.
 */
export default function SodReportPanel({ companyId }: { companyId: number | null }) {
  const { t } = useTranslation()
  const report = useSodReport(companyId, true)

  if (report.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(report.error)}</div>
  if (!report.data) return null
  const data = report.data
  return (
    <Panel title={t('duties.report.title')}>
      <dl className="grid grid-cols-2 gap-px border-b border-surface-700 bg-surface-700 sm:grid-cols-5">
        {STATUSES.map(s => {
          const { icon: Icon } = STATUS_LOOK[s]
          return (
            <div key={s} className="bg-surface-900 px-3 py-2">
              <dt className="text-meta flex items-center gap-1"><Icon size={11} aria-hidden="true" />{t(`duties.report.status.${s}`)}</dt>
              <dd className="mt-0.5 text-lg font-semibold tabular-nums text-gray-100">{data.summary[s]}</dd>
            </div>
          )
        })}
      </dl>
      <div className="space-y-4 py-3">
        <div>
          <h3 className="flex items-center gap-1.5 px-3 text-[13px] font-semibold text-gray-100">
            <TriangleAlert size={13} aria-hidden="true" className="text-gray-400" />{t('duties.report.violations')}
          </h3>
          {data.violations.length === 0
            ? <p className="px-3 pt-1 text-xs text-gray-400">{t('duties.report.noViolations')}</p>
            : <Approvals rows={data.violations} label={t('duties.report.violations')} />}
        </div>
        <details>
          <summary className="mx-3 cursor-pointer rounded-sm text-xs text-gray-300 hover:text-white">
            {t('duties.report.allApprovals', { count: data.approvals.length })}
          </summary>
          <Approvals rows={data.approvals} label={t('duties.report.allApprovalsLabel')} />
        </details>
        <Matrix matrix={data.matrix} />
        {data.refusals.length > 0 && (
          <p className="px-3 text-xs text-gray-400">{t('duties.report.refusals', { count: data.refusals.length })}</p>
        )}
      </div>
    </Panel>
  )
}

function Approvals({ rows, label }: { rows: SodApproval[]; label: string }) {
  const { t } = useTranslation()
  return (
    <div className="mt-2 overflow-x-auto">
      <table className={TABLE} aria-label={label}>
        <thead>
          <tr>
            <th className={TH}>{t('duties.report.action')}</th>
            <th className={TH}>{t('duties.report.subject')}</th>
            <th className={TH}>{t('duties.report.requester')}</th>
            <th className={TH}>{t('duties.report.approver')}</th>
            <th className={TH}>{t('duties.report.statusLabel')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(a => {
            const look = STATUS_LOOK[a.status] ?? UNKNOWN_LOOK
            return (
              <tr key={`${a.action}-${a.subject.kind}-${a.subject.id}-${a.at}`} className={TR}>
                <td className={TD}>{t(`duties.actions.${a.action}`, { defaultValue: a.action })}</td>
                <td className={TD}>{a.subject.label}</td>
                <td className={TD}>{a.requesters.map(r => r.label ?? '—').join(', ') || '—'}</td>
                <td className={TD}>{a.approver.label ?? '—'}</td>
                <td className={TD}>
                  <StatusBadge icon={look.icon} tone={look.tone} label={t(`duties.report.status.${a.status}`)} />
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function Matrix({ matrix }: { matrix: SodReport['matrix'] }) {
  const { t } = useTranslation()
  const rows = [
    ...matrix.members.map(m => ({ key: `m${m.user_id}`, name: m.label, role: m.effective_role ?? t('duties.report.formerMember'), flag: m.holds_request_and_approval, counts: m })),
    ...matrix.roles.map(r => ({ key: `r${r.role}`, name: t('duties.report.allWithRole', { role: r.role }), role: r.role, flag: false, counts: r })),
    ...matrix.agents.map(a => ({ key: `a${a.role}`, name: a.role, role: t('duties.report.agent'), flag: false, counts: a })),
  ]
  return (
    <div className="overflow-x-auto">
      <table className={TABLE} aria-label={t('duties.report.matrix')}>
        <caption className="px-3 pb-1 text-left text-[13px] font-semibold text-gray-100">{t('duties.report.matrix')}</caption>
        <thead>
          <tr>
            <th className={TH}>{t('duties.report.who')}</th>
            <th className={TH}>{t('duties.report.role')}</th>
            {matrix.duties.map(d => <th key={d} className={`${TH} text-right`}>{t(`duties.duty.${d}`)}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.key} className={TR}>
              <td className={TD}>
                {row.name}
                {row.flag && (
                  <span className="ml-1.5 inline-flex items-center gap-1 text-[11px] text-amber-300">
                    <ShieldAlert size={11} aria-hidden="true" />{t('duties.report.both')}
                  </span>
                )}
              </td>
              <td className={`${TD} text-gray-400`}>{row.role}</td>
              {matrix.duties.map(d => <td key={d} className={`${TD} text-right tabular-nums`}>{row.counts[d]}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
