import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ShieldCheck,
  ShieldAlert,
  AlertTriangle,
  Clock,
  ArrowRight,
  Code,
  FileCheck2,
  FileX2,
  HelpCircle,
  Terminal,
} from 'lucide-react'
import { FittsPrimaryActions } from './FittsPrimaryActions'

export interface ArtifactValidation {
  id: string
  name: string
  type: 'code' | 'model' | 'configuration' | 'evidence'
  status: 'passed' | 'failed' | 'warning' | 'pending'
  summary: string
  score?: number
  timestamp: string
  details?: {
    ast_valid?: boolean
    safety_checks?: string[]
    policy_violations?: string[]
    execution_time_ms?: number
  }
}

export interface ValidationGateProps {
  gateState?: 'idle' | 'running' | 'passed' | 'blocked'
  artifacts?: ArtifactValidation[]
  onRunAudit?: () => Promise<void> | void
  onExportGraph?: () => Promise<void> | void
  pipelineStage?: string
  lastEvaluatedAt?: string
  isLoading?: boolean
}

export const ValidationGateDashboard: React.FC<ValidationGateProps> = ({
  gateState = 'passed',
  artifacts = [],
  onRunAudit,
  onExportGraph,
  pipelineStage = 'ast_validation',
  lastEvaluatedAt,
  isLoading = false,
}) => {
  const { t } = useTranslation()
  const [internalRunning, setInternalRunning] = useState(false)
  const [internalExporting, setInternalExporting] = useState(false)

  const handleRunAudit = async () => {
    setInternalRunning(true)
    try {
      if (onRunAudit) {
        await onRunAudit()
      }
    } finally {
      setInternalRunning(false)
    }
  }

  const handleExportGraph = async () => {
    setInternalExporting(true)
    try {
      if (onExportGraph) {
        await onExportGraph()
      }
    } finally {
      setInternalExporting(false)
    }
  }

  const getStatusBadge = (status: ArtifactValidation['status']) => {
    switch (status) {
      case 'passed':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
            <ShieldCheck className="w-3.5 h-3.5" />
            {t('validationGate.passed', { defaultValue: 'PASSED' })}
          </span>
        )
      case 'failed':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
            <ShieldAlert className="w-3.5 h-3.5" />
            {t('validationGate.failed', { defaultValue: 'BLOCKED' })}
          </span>
        )
      case 'warning':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-400 border border-amber-500/20">
            <AlertTriangle className="w-3.5 h-3.5" />
            {t('validationGate.warning', { defaultValue: 'WARNING' })}
          </span>
        )
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-neutral-500/10 text-neutral-400 border border-neutral-500/20">
            <Clock className="w-3.5 h-3.5" />
            {t('validationGate.pending', { defaultValue: 'PENDING' })}
          </span>
        )
    }
  }

  return (
    <div className="relative min-h-[600px] w-full p-6 space-y-6 text-neutral-100 bg-neutral-950 rounded-2xl border border-neutral-800 shadow-xl pb-24">
      {/* High Visibility Header with Top-Right Action Placement for Wide Displays */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-neutral-800">
        <div>
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-blue-500/10 border border-blue-500/20 text-blue-400">
              <ShieldCheck className="w-6 h-6" />
            </div>
            <div>
              <h2 className="text-xl font-bold tracking-tight text-white">
                {t('validationGate.title', { defaultValue: 'LangGraph Validation Gate & Verification' })}
              </h2>
              <p className="text-sm text-neutral-400">
                {t('validationGate.subtitle', {
                  defaultValue: 'Continuous AST safety guarantees and graph-based execution provenance',
                })}
              </p>
            </div>
          </div>
        </div>

        {/* Top-Right Corner Placement Area */}
        <div className="flex items-center gap-3">
          <div className="text-right text-xs text-neutral-400 pr-2 hidden lg:block">
            <div className="font-mono text-neutral-300">Stage: {pipelineStage}</div>
            <div>{lastEvaluatedAt ? `Evaluated ${lastEvaluatedAt}` : 'Real-time telemetry'}</div>
          </div>
        </div>
      </div>

      {/* Main LangGraph Pipeline Status View */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="p-4 rounded-xl bg-neutral-900 border border-neutral-800">
          <div className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-1">Gate Decision</div>
          <div className="text-lg font-bold flex items-center gap-2">
            {gateState === 'passed' && <span className="text-emerald-400">PASS (Execution Cleared)</span>}
            {gateState === 'blocked' && <span className="text-rose-400">BLOCKED (Safety Policy)</span>}
            {gateState === 'running' && <span className="text-blue-400">ANALYZING AST GRAPH...</span>}
            {gateState === 'idle' && <span className="text-neutral-400">IDLE (Awaiting Trigger)</span>}
          </div>
        </div>
        <div className="p-4 rounded-xl bg-neutral-900 border border-neutral-800">
          <div className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-1">Evaluated Node</div>
          <div className="text-lg font-mono font-medium text-neutral-200 flex items-center gap-2">
            <Terminal className="w-4 h-4 text-cyan-400" />
            {pipelineStage}
          </div>
        </div>
        <div className="p-4 rounded-xl bg-neutral-900 border border-neutral-800">
          <div className="text-xs font-semibold uppercase tracking-wider text-neutral-400 mb-1">Artifacts In Scope</div>
          <div className="text-lg font-bold text-neutral-100 flex items-center gap-2">
            <Code className="w-4 h-4 text-indigo-400" />
            {artifacts.length} verified artifacts
          </div>
        </div>
      </div>

      {/* Artifact Verification Table */}
      <div className="overflow-hidden rounded-xl border border-neutral-800 bg-neutral-900">
        <div className="px-5 py-3.5 border-b border-neutral-800 font-semibold text-sm text-neutral-300 flex items-center justify-between">
          <span>Target Code & Artifact Ledger</span>
          <span className="text-xs font-mono text-neutral-500">Fitts-optimized workspace</span>
        </div>
        {artifacts.length === 0 ? (
          <div className="p-8 text-center text-neutral-500">
            <HelpCircle className="w-8 h-8 mx-auto mb-2 text-neutral-600" />
            <p>{t('validationGate.noArtifacts', { defaultValue: 'No artifacts recorded in the current LangGraph state.' })}</p>
          </div>
        ) : (
          <div className="divide-y divide-neutral-800">
            {artifacts.map((artifact) => (
              <div key={artifact.id} className="p-4 hover:bg-neutral-800/40 transition-colors flex items-center justify-between gap-4">
                <div className="flex items-center gap-3">
                  {artifact.status === 'passed' ? (
                    <FileCheck2 className="w-5 h-5 text-emerald-400 shrink-0" />
                  ) : (
                    <FileX2 className="w-5 h-5 text-rose-400 shrink-0" />
                  )}
                  <div>
                    <div className="font-medium text-neutral-200 text-sm font-mono">{artifact.name}</div>
                    <div className="text-xs text-neutral-400">{artifact.summary}</div>
                  </div>
                </div>
                <div className="flex items-center gap-4 shrink-0">
                  {getStatusBadge(artifact.status)}
                  <ArrowRight className="w-4 h-4 text-neutral-600" />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Fitts's Law Optimized Primary Action Dock (Bottom-Right Anchor) */}
      <FittsPrimaryActions
        onRunAudit={handleRunAudit}
        onExportGraph={handleExportGraph}
        isRunningAudit={internalRunning || isLoading || gateState === 'running'}
        isExportingGraph={internalExporting}
        placement="bottom-right"
      />
    </div>
  )
}

export default ValidationGateDashboard
