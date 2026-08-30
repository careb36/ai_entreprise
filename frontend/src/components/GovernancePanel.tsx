import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { 
  usePendingApprovals, 
  useApproveTask,
  useQuotas,
  useResetQuota,
} from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'
import { ShieldCheck, CheckCircle2, XCircle, Loader2, RefreshCw, AlertTriangle, Info, RotateCcw } from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface PendingTask {
  id: number
  interaction_id: string
  task_type: string
  description: string
  requested_by: string
  risk_level?: string
  created_at?: string
}

// ── Risk color map ────────────────────────────────────────────────────────────

const RISK_CONFIG: Record<string, { badge: string; border: string }> = {
  DATABASE_SCHEMA_CHANGE:  { badge: 'badge bg-red-900/60 text-red-300 border-red-700/40',    border: 'border-l-red-500' },
  PRODUCTION_DEPLOYMENT:   { badge: 'badge bg-orange-900/60 text-orange-300 border-orange-700/40', border: 'border-l-orange-500' },
  INFRASTRUCTURE_CHANGE:   { badge: 'badge bg-yellow-900/60 text-yellow-300 border-yellow-700/40', border: 'border-l-yellow-500' },
  CODE_MERGE:              { badge: 'badge bg-blue-900/60 text-blue-300 border-blue-700/40',   border: 'border-l-blue-500' },
  DEFAULT:                 { badge: 'badge bg-surface-700 text-gray-300',                      border: 'border-l-surface-600' },
}

function riskConfig(taskType: string) {
  return RISK_CONFIG[taskType] ?? RISK_CONFIG.DEFAULT
}

// ── Confirmation modal ────────────────────────────────────────────────────────

function ConfirmModal({
  task, decision, onConfirm, onCancel, loading
}: {
  task: PendingTask
  decision: 'approved' | 'rejected'
  onConfirm: (feedback?: string) => void
  onCancel: () => void
  loading: boolean
}) {
  const { t } = useTranslation()
  const isApprove = decision === 'approved'
  const [feedback, setFeedback] = useState('')
  const [feedbackError, setFeedbackError] = useState(false)

  function handleConfirm() {
    if (!isApprove && !feedback.trim()) {
      setFeedbackError(true)
      return
    }
    onConfirm(feedback || undefined)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm animate-fade-in">
      <div className="card max-w-md w-full p-6 shadow-card-md animate-slide-up">
        <div className={`flex items-center gap-3 mb-4`}>
          {isApprove
            ? <CheckCircle2 className="text-status-success" size={22} />
            : <XCircle className="text-status-error" size={22} />}
          <h3 className="font-semibold text-white text-lg">
            {isApprove ? t('governance.confirmApprove') : t('governance.confirmReject')}
          </h3>
        </div>

        <div className="bg-surface-800 rounded-lg p-3 mb-4 border border-surface-700">
          <p className="text-sm text-gray-300 leading-relaxed">{task.description}</p>
          <p className="text-xs text-gray-500 mt-2 font-mono">{task.interaction_id}</p>
        </div>

        <div className="mb-5">
          <label className="input-label flex items-center gap-1.5">
            <Info size={12} />
            {t('governance.feedbackLabel')}
            {!isApprove && <span className="text-status-error ml-0.5">*</span>}
            {isApprove && <span className="text-gray-600 font-normal">({t('common.optional', t('common.na'))})</span>}
          </label>
          <textarea
            value={feedback}
            onChange={e => { setFeedback(e.target.value); setFeedbackError(false) }}
            rows={3}
            placeholder={isApprove ? t('governance.feedbackPlaceholderApprove') : t('governance.feedbackPlaceholderReject')}
            className={`textarea w-full text-sm ${feedbackError ? 'border-status-error' : ''}`}
          />
          {feedbackError && (
            <p className="text-xs text-status-error mt-1 flex items-center gap-1">
              <AlertTriangle size={11} />
              {t('governance.feedbackRequired')}
            </p>
          )}
        </div>

        <div className="flex gap-3 justify-end">
          <button onClick={onCancel} disabled={loading} className="btn btn-ghost">
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            onClick={handleConfirm}
            disabled={loading}
            className={`btn ${isApprove ? 'btn-success' : 'btn-danger'}`}
          >
            {loading && <Loader2 size={14} className="animate-spin" />}
            {isApprove ? t('governance.approve') : t('governance.reject')}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── GovernancePanel ───────────────────────────────────────────────────────────

export const GovernancePanel: React.FC = () => {
  const { t } = useTranslation()
  const { companyId } = useAppStore()
  const { data, isLoading, isError, refetch } = usePendingApprovals()
  const approveMutation = useApproveTask()
  const { data: quotaData } = useQuotas(companyId)
  const resetQuotaMutation = useResetQuota()

  const [confirm, setConfirm] = useState<{ task: PendingTask; decision: 'approved' | 'rejected' } | null>(null)
  const [page, setPage] = useState(0)
  const [pendingResets, setPendingResets] = useState<Set<number>>(new Set())
  const PER_PAGE = 10

  const tasks = (data?.pending_approvals || []) as PendingTask[]
  const actioningId = approveMutation.isPending ? approveMutation.variables?.taskId : null

  async function executeDecision(taskId: number, decision: 'approved' | 'rejected', feedback?: string) {
    approveMutation.mutate({ taskId, decision, approverName: 'Manager', feedback }, {
      onSettled: () => setConfirm(null)
    })
  }

  const paginated = tasks.slice(page * PER_PAGE, (page + 1) * PER_PAGE)
  const totalPages = Math.ceil(tasks.length / PER_PAGE)

  const error = (approveMutation.error as any)?.message || (isError ? t('governance.fetchError') : null)

  // ── Empty state ─────────────────────────────────────────────────────────────

  if (!isLoading && tasks.length === 0) {
    return (
      <div className="empty-state py-16">
        <CheckCircle2 size={40} className="text-status-success mx-auto mb-3" />
        <p className="font-semibold text-gray-300">{t('governance.noPending')}</p>
        <p className="text-sm text-gray-500 mt-1">{t('governance.allActionsOk')}</p>
        <button onClick={() => refetch()} className="btn btn-ghost btn-sm mt-4 flex items-center gap-1.5 mx-auto">
          <RefreshCw size={13} /> {t('common.refresh', 'Refresh')}
        </button>
      </div>
    )
  }

  return (
    <div>
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3 mb-6">
        <div className="flex items-center gap-2">
          <ShieldCheck className="text-brand-400" size={22} />
          <h2 className="text-xl font-bold text-white">{t('governance.title')}</h2>
        </div>
        {tasks.length > 0 && (
          <span className="badge bg-red-900/60 text-red-300 border-red-700/40 animate-pulse">
            {tasks.length} {t('governance.pending')}
          </span>
        )}
        <button onClick={() => refetch()} disabled={isLoading} className="btn-ghost btn-sm flex items-center gap-1.5 ml-auto">
          <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
          <span className="hidden sm:inline">{t('common.refresh', 'Refresh')}</span>
        </button>
      </div>

      {/* Token Quota Usage Bars */}
      {quotaData && Array.isArray(quotaData) && quotaData.length > 0 && (
        <div className="mb-6">
          <h3 className="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">
            {t('governance.quotaTitle')}
          </h3>
          <div className="space-y-3">
            {quotaData.map((unit: any) => {
              const pct = unit.limit > 0 ? Math.min(100, Math.round((unit.used / unit.limit) * 100)) : 0
              const isWarning = pct >= 80 && pct < 100
              const isExceeded = unit.limit > 0 && unit.used >= unit.limit
              const barColor = isExceeded
                ? 'bg-status-error'
                : isWarning
                ? 'bg-yellow-500'
                : 'bg-brand-500'
              return (
                <div key={unit.id ?? unit.unit} className="card p-3">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-medium text-gray-300">{unit.unit}</span>
                    <div className="flex items-center gap-2">
                      {isExceeded && (
                        <span className="badge bg-red-900/60 text-red-300 border-red-700/40 text-xs">
                          {t('governance.quotaExceeded')}
                        </span>
                      )}
                      {isWarning && !isExceeded && (
                        <span className="badge bg-yellow-900/60 text-yellow-300 border-yellow-700/40 text-xs flex items-center gap-1">
                          <AlertTriangle size={10} />
                          {t('governance.quotaWarning')}
                        </span>
                      )}
                      <span className="text-xs text-gray-500">
                        {unit.used.toLocaleString()} / {unit.limit > 0 ? unit.limit.toLocaleString() : '∞'} {t('governance.tokens')}
                      </span>
                      {unit.id && (
                        <button
                          onClick={() => {
                            setPendingResets(prev => new Set(prev).add(unit.id))
                            resetQuotaMutation.mutate(unit.id, {
                              onSettled: () => setPendingResets(prev => { const s = new Set(prev); s.delete(unit.id); return s })
                            })
                          }}
                          disabled={pendingResets.has(unit.id)}
                          className="btn-ghost btn-sm p-1"
                          title={t('governance.resetQuota')}
                        >
                          <RotateCcw size={12} className={pendingResets.has(unit.id) ? 'animate-spin' : ''} />
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="w-full bg-surface-700 rounded-full h-2">
                    <div
                      className={`h-2 rounded-full transition-all duration-500 ${barColor}`}
                      style={{ width: `${unit.limit > 0 ? pct : 0}%` }}
                    />
                  </div>
                  {unit.limit > 0 && (
                    <p className="text-xs text-gray-600 mt-1 text-right">{pct}%</p>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {error && <div className="alert-error mb-4 flex items-center gap-2"><AlertTriangle size={14} />{error}</div>}

      {isLoading ? (
        <div className="space-y-3">
          {[1,2,3].map(i => <div key={i} className="skeleton h-24 rounded-lg" />)}
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {paginated.map(task => {
              const rc = riskConfig(task.task_type)
              return (
                <div
                  key={task.id}
                  className={`card border-l-4 ${rc.border} p-4 flex flex-col sm:flex-row gap-4 justify-between`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-2">
                      <span className="badge bg-surface-700 text-gray-300 text-xs font-semibold">
                        {t('governance.requestedBy', 'By')}: {task.requested_by}
                      </span>
                      <span className={rc.badge}>
                        {t(`governance.taskTypes.${task.task_type}`, task.task_type.replace(/_/g, ' '))}
                      </span>
                    </div>
                    <p className="text-gray-200 text-sm leading-relaxed">{task.description}</p>
                    <p className="text-xs text-gray-500 font-mono mt-1.5 flex items-center gap-1">
                      <Info size={10} />
                      {t('governance.interactionId', 'ID')}: {task.interaction_id}
                    </p>
                  </div>

                  <div className="flex items-center gap-2 flex-shrink-0">
                    <button
                      onClick={() => setConfirm({ task, decision: 'rejected' })}
                      disabled={actioningId === task.id}
                      className="btn btn-danger btn-sm"
                    >
                      {actioningId === task.id ? <Loader2 size={12} className="animate-spin" /> : <XCircle size={13} />}
                      {t('governance.reject')}
                    </button>
                    <button
                      onClick={() => setConfirm({ task, decision: 'approved' })}
                      disabled={actioningId === task.id}
                      className="btn btn-success btn-sm"
                    >
                      {actioningId === task.id ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle2 size={13} />}
                      {t('governance.approve')}
                    </button>
                  </div>
                </div>
              )
            })}
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-center gap-2 mt-6">
              <button onClick={() => setPage(p => p - 1)} disabled={page === 0} className="btn-ghost btn-sm" aria-label="Previous page">←</button>

              <span className="text-sm text-gray-400">{page + 1} / {totalPages}</span>
              <button onClick={() => setPage(p => p + 1)} disabled={page >= totalPages - 1} className="btn-ghost btn-sm" aria-label="Next page">→</button>
            </div>
          )}
        </>
      )}

      {/* Confirmation modal */}
      {confirm && (
        <ConfirmModal
          task={confirm.task}
          decision={confirm.decision}
          loading={actioningId === confirm.task.id}
          onConfirm={(feedback) => executeDecision(confirm.task.id, confirm.decision, feedback)}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  )
}
