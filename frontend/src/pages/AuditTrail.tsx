import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { CircleDashed, ShieldCheck, ShieldQuestion, ShieldX, type LucideIcon } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import { useVerifyChain, type AuditChainReport, type ChainStatus } from '../services/auditTrail'
import { useAppStore } from '../store/useAppStore'
import { CountPill, PageHeader, Panel, Spinner, StatusBadge, type StatusTone, TABLE, TD, TH, TR } from '../components/ui'
import { CounterSign } from './auditTrail/CounterSign'

export const CHAIN_LOOK: Record<ChainStatus, { icon: LucideIcon; tone: StatusTone }> = {
  intact: { icon: ShieldCheck, tone: 'success' },
  broken: { icon: ShieldX, tone: 'danger' },
  empty: { icon: CircleDashed, tone: 'neutral' },
}

/**
 * The audit trail (`/company/audit-trail`), for every member: the company's
 * audit chain walked end to end by the backend on request, its head to record
 * elsewhere, where it breaks, and the head's statement to counter-sign with
 * the company's own key. The verdicts are the server's.
 */
export default function AuditTrail() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const verify = useVerifyChain(companyId)

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader title={t('auditTrail.title')} subtitle={t('auditTrail.subtitle')} />
      <Panel
        title={t('auditTrail.chainTitle')}
        actions={(
          <button type="button" className="btn-tool border border-surface-700" disabled={verify.isPending || companyId === null}
            onClick={() => verify.mutate()}>
            {verify.isPending ? <Spinner size={13} /> : <ShieldQuestion size={13} aria-hidden="true" />}
            {verify.data ? t('auditTrail.verifyAgain') : t('auditTrail.verify')}
          </button>
        )}>
        <div className="space-y-3 p-3" aria-live="polite">
          {!verify.data && !verify.isError && !verify.isPending && <p className="text-xs text-gray-400">{t('auditTrail.verifyHint')}</p>}
          {verify.isPending && <p role="status" className="text-xs text-gray-400">{t('auditTrail.verifying')}</p>}
          {verify.isError && <p role="alert" className="alert-error text-sm">{t('auditTrail.verifyError', { message: errorDetail(verify.error) })}</p>}
          {verify.data && <ChainReport report={verify.data} canManage={canManage} />}
        </div>
      </Panel>
      <CounterSign />
    </div>
  )
}

function ChainReport({ report, canManage }: { report: AuditChainReport; canManage: boolean }) {
  const { t } = useTranslation()
  const look = CHAIN_LOOK[report.status] ?? CHAIN_LOOK.empty
  const breaksId = useId()

  return (
    <div className="space-y-3">
      <p className="flex flex-wrap items-center gap-2 text-[13px]">
        <StatusBadge icon={look.icon} tone={look.tone} label={t(`auditTrail.status.${report.status}`)} />
        <span className="text-gray-300">{t(`auditTrail.statusText.${report.status}`)}</span>
      </p>
      <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-md border border-surface-700 bg-surface-700 sm:grid-cols-3">
        <div className="bg-surface-900 px-3 py-2">
          <dt className="text-meta">{t('auditTrail.rows')}</dt>
          <dd className="mt-0.5 text-lg font-semibold tabular-nums text-gray-100">{report.rows}</dd>
        </div>
        <div className="bg-surface-900 px-3 py-2">
          <dt className="text-meta">{t('auditTrail.headSeq')}</dt>
          <dd className="mt-0.5 text-lg font-semibold tabular-nums text-gray-100">{report.head?.seq ?? '—'}</dd>
        </div>
        <div className="bg-surface-900 px-3 py-2">
          <dt className="text-meta">{t('auditTrail.mode')}</dt>
          <dd className="mt-0.5 text-[13px] text-gray-100">{report.mode ? t(`auditTrail.modes.${report.mode}`) : '—'}</dd>
        </div>
      </dl>
      {report.head && (
        <div>
          <p className="text-meta">{t('auditTrail.headHash')}</p>
          <code className="block break-all font-mono text-xs text-gray-200">{report.head.chain_hash}</code>
          <p className="text-meta mt-1">{t('auditTrail.headHint')}</p>
        </div>
      )}
      {report.breaks.length > 0 && (
        <section aria-labelledby={breaksId} role="alert" className="rounded-md border border-red-700/50">
          <h3 id={breaksId} className="flex items-center gap-2 px-3 py-2 text-[13px] font-medium text-red-300">
            <ShieldX size={13} aria-hidden="true" />{t('auditTrail.breaksTitle')}
            <CountPill count={report.breaks.length} label={t('auditTrail.breakCount', { count: report.breaks.length })} />
          </h3>
          <table className={TABLE}>
            <thead>
              <tr>
                <th scope="col" className={`${TH} text-right`}>{t('auditTrail.columns.seq')}</th>
                <th scope="col" className={TH}>{t('auditTrail.columns.reason')}</th>
              </tr>
            </thead>
            <tbody>
              {report.breaks.map(b => (
                <tr key={`${b.seq}-${b.reason}`} className={TR}>
                  <td className={`${TD} text-right tabular-nums`}>{b.seq}</td>
                  <td className={TD}>{t(`auditTrail.reasons.${b.reason}`, { defaultValue: b.reason })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      <p className="text-xs text-gray-400">
        {t(`auditAnchors.status.${report.anchors.status}`)}{' '}
        {canManage && <Link to="/company/audit-anchors" className="text-brand-300 hover:underline">{t('auditTrail.anchorsLink')}</Link>}
      </p>
    </div>
  )
}
