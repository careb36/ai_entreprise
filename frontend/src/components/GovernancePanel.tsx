import React, { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { api } from '../services/api'
import { ShieldCheck, CheckCircle2, XCircle, Loader2, RefreshCw, AlertTriangle, Info } from 'lucide-react'

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
  onConfirm: () => void
  onCancel: () => void
  loading: boolean
}) {
  const { t } = useTranslation()
  const isApprove = decision === 'approved'

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

        <div className="bg-surface-800 rounded-lg p-3 mb-5 border border-surface-700">
          <p className="text-sm text-gray-300 leading-relaxed">{task.description}</p>
          <p className="text-xs text-gray-500 mt-2 font-mono">{task.interaction_id}</p>
        </div>

        <div className="flex gap-3 justify-end">
          <button onClick={onCancel} disabled={loading} className="btn btn-ghost">
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            onClick={onConfirm}
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
  const [tasks, setTasks] = useState<PendingTask[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actioningId, setActioningId] = useState<number | null>(null)
  const [confirm, setConfirm] = useState<{ task: PendingTask; decision: 'approved' | 'rejected' } | null>(null)
  const [page, setPage] = useState(0)
  const PER_PAGE = 10

  const fetchTasks = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.get<{ pending_approvals: PendingTask[] }>('/v1/pending_approvals')
      setTasks(res.pending_approvals ?? [])
    } catch {
      // Fallback to empty — backend may not have any pending
      setTasks([])
      setError(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchTasks() }, [fetchTasks])

  async function executeDecision(taskId: number, decision: 'approved' | 'rejected') {
    setActioningId(taskId)
    setConfirm(null)
    try {
      await api.approveTask(taskId, 'Manager', decision)
      setTasks(prev => prev.filter(t => t.id !== taskId))
    } catch (e: any) {
      setError(e.message ?? t('governance.actionError', 'Action failed'))
    } finally {
      setActioningId(null)
    }
  }

  const paginated = tasks.slice(page * PER_PAGE, (page + 1) * PER_PAGE)
  const totalPages = Math.ceil(tasks.length / PER_PAGE)

  // ── Empty state ─────────────────────────────────────────────────────────────

  if (!loading && tasks.length === 0) {
    return (
      <div className="empty-state py-16">
        <CheckCircle2 size={40} className="text-status-success mx-auto mb-3" />
        <p className="font-semibold text-gray-300">{t('governance.noPending')}</p>
        <p className="text-sm text-gray-500 mt-1">{t('governance.noPendingHint', 'All AI actions are within autonomy limits')}</p>
        <button onClick={fetchTasks} className="btn btn-ghost btn-sm mt-4 flex items-center gap-1.5 mx-auto">
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
        <button onClick={fetchTasks} disabled={loading} className="btn-ghost btn-sm flex items-center gap-1.5 ml-auto">
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          <span className="hidden sm:inline">{t('common.refresh', 'Refresh')}</span>
        </button>
      </div>

      {error && <div className="alert-error mb-4 flex items-center gap-2"><AlertTriangle size={14} />{error}</div>}

      {loading ? (
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
          onConfirm={() => executeDecision(confirm.task.id, confirm.decision)}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  )
}

