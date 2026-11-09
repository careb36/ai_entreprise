import React from 'react'
import { Play, Download, Loader2, Sparkles, Network } from 'lucide-react'

export interface FittsPrimaryActionsProps {
  onRunAudit?: () => void | Promise<void>
  onExportGraph?: () => void | Promise<void>
  isRunningAudit?: boolean
  isExportingGraph?: boolean
  auditDisabled?: boolean
  exportDisabled?: boolean
  placement?: 'bottom-right' | 'top-right'
  className?: string
}

/**
 * FittsPrimaryActions
 *
 * Implements Fitts's Law UX optimization:
 * 1. Target Size (W >= 44x44px): Touch targets are sized at a minimum of 44px
 *    height and width (WCAG 2.5.5 Target Size Enhanced) with generous hit areas.
 * 2. Corner Placement (D -> 0 overshoot): Positioned at screen corners (bottom-right
 *    or top-right) where viewport boundaries act as natural motion stops, minimizing
 *    movement time and motor acquisition errors.
 */
export const FittsPrimaryActions: React.FC<FittsPrimaryActionsProps> = ({
  onRunAudit,
  onExportGraph,
  isRunningAudit = false,
  isExportingGraph = false,
  auditDisabled = false,
  exportDisabled = false,
  placement = 'bottom-right',
  className = '',
}) => {
  const placementClasses =
    placement === 'bottom-right'
      ? 'fixed bottom-6 right-6 z-40'
      : 'fixed top-4 right-6 z-40'

  return (
    <aside
      aria-label="Primary Quick Actions"
      data-testid="fitts-primary-actions"
      className={`${placementClasses} flex items-center gap-3 bg-neutral-900/90 dark:bg-neutral-950/90 backdrop-blur-md p-2 rounded-2xl border border-neutral-700/60 shadow-2xl transition-all duration-200 ease-in-out hover:shadow-cyan-500/10 ${className}`}
    >
      {/* Export Graph Button - Minimum touch target 44x44px */}
      <button
        type="button"
        data-testid="export-graph-btn"
        aria-label="Export Knowledge Graph"
        disabled={exportDisabled || isExportingGraph}
        onClick={onExportGraph}
        className="group relative inline-flex items-center justify-center gap-2 min-h-[44px] min-w-[44px] h-12 px-4 py-2.5 rounded-xl font-medium text-sm text-neutral-200 bg-neutral-800 hover:bg-neutral-700 active:scale-[0.98] border border-neutral-600/50 hover:border-neutral-500 transition-all focus:outline-none focus:ring-2 focus:ring-cyan-400 focus:ring-offset-2 focus:ring-offset-neutral-900 disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none"
      >
        {isExportingGraph ? (
          <Loader2 className="w-5 h-5 text-cyan-400 animate-spin" aria-hidden="true" />
        ) : (
          <Network className="w-5 h-5 text-cyan-400 group-hover:scale-110 transition-transform" aria-hidden="true" />
        )}
        <span className="hidden sm:inline font-semibold tracking-wide">Export Graph</span>
        <Download className="w-4 h-4 text-neutral-400 hidden sm:inline ml-0.5" aria-hidden="true" />
      </button>

      {/* Run AI Audit Button - Minimum touch target 44x44px, High-Emphasis Primary */}
      <button
        type="button"
        data-testid="run-ai-audit-btn"
        aria-label="Run AI Audit"
        disabled={auditDisabled || isRunningAudit}
        onClick={onRunAudit}
        className="group relative inline-flex items-center justify-center gap-2.5 min-h-[44px] min-w-[44px] h-12 px-5 py-2.5 rounded-xl font-semibold text-sm text-white bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 active:scale-[0.98] shadow-lg shadow-indigo-600/30 hover:shadow-indigo-600/50 border border-blue-400/30 transition-all focus:outline-none focus:ring-2 focus:ring-blue-400 focus:ring-offset-2 focus:ring-offset-neutral-900 disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none"
      >
        {isRunningAudit ? (
          <Loader2 className="w-5 h-5 text-white animate-spin" aria-hidden="true" />
        ) : (
          <Sparkles className="w-5 h-5 text-amber-300 group-hover:rotate-12 transition-transform" aria-hidden="true" />
        )}
        <span className="font-bold tracking-wide">Run AI Audit</span>
        {!isRunningAudit && <Play className="w-4 h-4 fill-white text-white ml-0.5" aria-hidden="true" />}
      </button>
    </aside>
  )
}

export default FittsPrimaryActions
