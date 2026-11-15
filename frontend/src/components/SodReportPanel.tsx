import { useTranslation } from 'react-i18next'
import { useSodReport } from '../hooks/useDuties'
import { errorDetail, type SodApproval, type SodReport } from '../services/api'

const STATUSES = ['separated', 'exception', 'violation', 'unattributed', 'not_a_person'] as const

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
    <section className="card p-4 space-y-4" aria-label={t('duties.report.title')}>
      <h2 className="text-base font-semibold text-gray-100">{t('duties.report.title')}</h2>
      <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-5">
        {STATUSES.map(s => (
          <div key={s}>
            <dt className="text-xs text-gray-400">{t(`duties.report.status.${s}`)}</dt>
            <dd className="text-gray-100">{data.summary[s]}</dd>
          </div>
        ))}
      </dl>
      <div>
        <h3 className="text-sm font-semibold text-gray-200">{t('duties.report.violations')}</h3>
        {data.violations.length === 0
          ? <p className="text-xs text-gray-400">{t('duties.report.noViolations')}</p>
          : <Approvals rows={data.violations} label={t('duties.report.violations')} />}
      </div>
      <details>
        <summary className="cursor-pointer text-sm text-gray-200">
          {t('duties.report.allApprovals', { count: data.approvals.length })}
        </summary>
        <Approvals rows={data.approvals} label={t('duties.report.allApprovalsLabel')} />
      </details>
      <Matrix matrix={data.matrix} />
      {data.refusals.length > 0 && (
        <p className="text-xs text-gray-400">{t('duties.report.refusals', { count: data.refusals.length })}</p>
      )}
    </section>
  )
}

function Approvals({ rows, label }: { rows: SodApproval[]; label: string }) {
  const { t } = useTranslation()
  return (
    <table className="mt-2 w-full text-sm" aria-label={label}>
      <thead className="text-left text-xs text-gray-400">
        <tr>
          <th className="py-1">{t('duties.report.action')}</th>
          <th>{t('duties.report.subject')}</th>
          <th>{t('duties.report.requester')}</th>
          <th>{t('duties.report.approver')}</th>
          <th>{t('duties.report.statusLabel')}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(a => (
          <tr key={`${a.action}-${a.subject.kind}-${a.subject.id}-${a.at}`} className="border-t border-surface-700">
            <td className="py-1">{t(`duties.actions.${a.action}`, { defaultValue: a.action })}</td>
            <td>{a.subject.label}</td>
            <td>{a.requesters.map(r => r.label ?? '—').join(', ') || '—'}</td>
            <td>{a.approver.label ?? '—'}</td>
            <td className={a.status === 'violation' ? 'text-red-400' : a.status === 'exception' ? 'text-amber-300' : ''}>
              {t(`duties.report.status.${a.status}`)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
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
    <table className="w-full text-sm" aria-label={t('duties.report.matrix')}>
      <caption className="text-left text-sm font-semibold text-gray-200">{t('duties.report.matrix')}</caption>
      <thead className="text-left text-xs text-gray-400">
        <tr>
          <th className="py-1">{t('duties.report.who')}</th>
          <th>{t('duties.report.role')}</th>
          {matrix.duties.map(d => <th key={d}>{t(`duties.duty.${d}`)}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.map(row => (
          <tr key={row.key} className="border-t border-surface-700">
            <td className="py-1">
              {row.name}
              {row.flag && <span className="ml-1 text-xs text-amber-300">{t('duties.report.both')}</span>}
            </td>
            <td className="text-gray-400">{row.role}</td>
            {matrix.duties.map(d => <td key={d}>{row.counts[d]}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
