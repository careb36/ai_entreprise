import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Loader2, RotateCcw, ShieldCheck } from 'lucide-react'
import { useQuotas, useResetQuota } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'

export const QuotaPanel: React.FC = () => {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const { data: quotaData } = useQuotas(companyId)
  const resetQuotaMutation = useResetQuota()
  const [pendingResets, setPendingResets] = useState<Set<number>>(new Set())

  return (
    <div>
      <div className="mb-6 flex items-center gap-2">
        <ShieldCheck className="text-brand-400" size={22} />
        <h1 className="text-xl font-bold text-white">{t('governance.quotaTitle', { defaultValue: 'Token Quota Usage' })}</h1>
      </div>

      {Array.isArray(quotaData) && quotaData.length > 0 && (
        <section className="mb-6">
          <div className="space-y-3">
            {quotaData.map((unit: any) => {
              const pct = unit.limit > 0 ? Math.min(100, Math.round((unit.used / unit.limit) * 100)) : 0
              const isWarning = pct >= 80 && pct < 100
              const isExceeded = unit.limit > 0 && unit.used >= unit.limit
              const barColor = isExceeded ? 'bg-status-error' : isWarning ? 'bg-yellow-500' : 'bg-brand-500'
              return (
                <div key={unit.id ?? unit.unit} className="card p-3">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-gray-300">{unit.unit}</span>
                    <div className="flex items-center gap-2">
                      {isExceeded && <span className="badge bg-red-900/60 text-red-300">{t('governance.quotaExceeded')}</span>}
                      {isWarning && !isExceeded && <span className="badge bg-yellow-900/60 text-yellow-300"><AlertTriangle size={10} />{t('governance.quotaWarning')}</span>}
                      <span className="text-xs text-gray-500">
                        {unit.used.toLocaleString()} / {unit.limit > 0 ? unit.limit.toLocaleString() : '∞'} {t('governance.tokens')}
                      </span>
                      {unit.id && (
                        <button
                          onClick={() => {
                            setPendingResets(previous => new Set(previous).add(unit.id))
                            resetQuotaMutation.mutate(unit.id, {
                              onSettled: () => setPendingResets(previous => {
                                const next = new Set(previous)
                                next.delete(unit.id)
                                return next
                              }),
                            })
                          }}
                          disabled={pendingResets.has(unit.id)}
                          className="btn-ghost btn-sm p-1"
                          title={t('governance.resetQuota')}
                        >
                          {pendingResets.has(unit.id)
                            ? <Loader2 size={12} className="animate-spin" />
                            : <RotateCcw size={12} />}
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="h-2 w-full rounded-full bg-surface-700">
                    <div className={`h-2 rounded-full transition-all duration-500 ${barColor}`} style={{ width: `${unit.limit > 0 ? pct : 0}%` }} />
                  </div>
                  {unit.limit > 0 && <p className="mt-1 text-right text-xs text-gray-600">{pct}%</p>}
                </div>
              )
            })}
          </div>
        </section>
      )}

      {companyId === null && <div className="empty-state py-12"><AlertTriangle size={32} className="mx-auto mb-3 text-gray-500" /><p className="text-gray-400">{t('evidence.selectCompany')}</p></div>}
    </div>
  )
}
