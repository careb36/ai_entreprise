import React from 'react'

export type GraphSkeletonVariant = 'pipeline' | 'lineage' | 'topology' | 'default'

export interface GraphSkeletonProps {
  /** Visual variant matching the data fetch type (LangGraph pipeline vs Neo4j lineage) */
  variant?: GraphSkeletonVariant
  /** Optional custom title or section name */
  title?: string
  /** Whether to render the mock graph canvas controls/toolbar */
  showToolbar?: boolean
  /** Whether to render the node inspector/metadata side drawer */
  showSidebar?: boolean
  /** Number of simulated nodes to render in the canvas */
  nodesCount?: number
  /** Additional container styling */
  className?: string
  /** Test identifier */
  dataTestId?: string
}

/**
 * GraphSkeleton
 * 
 * Replaces static loading spinners with structured, animated skeleton layouts.
 * Adheres to the Doherty Threshold (<400ms perceived response feedback)
 * by rendering immediate topological previews during LangGraph & Neo4j data queries.
 */
export const GraphSkeleton: React.FC<GraphSkeletonProps> = ({
  variant = 'default',
  title,
  showToolbar = true,
  showSidebar = false,
  nodesCount = 6,
  className = '',
  dataTestId = 'graph-skeleton',
}) => {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Loading graph visualization"
      data-testid={dataTestId}
      className={`relative w-full h-full min-h-[420px] flex flex-col rounded-xl border border-surface-700/60 bg-surface-900/90 overflow-hidden select-none ${className}`}
    >
      {/* Header bar / Breadcrumbs skeleton */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700/50 bg-surface-850/50 backdrop-blur-sm animate-pulse">
        <div className="flex items-center space-x-3">
          <div className="w-4 h-4 rounded-full bg-surface-700" />
          <div className="h-4 w-32 bg-surface-700 rounded" />
          {title && (
            <>
              <span className="text-surface-600 text-xs">/</span>
              <div className="h-4 w-24 bg-surface-700/70 rounded" />
            </>
          )}
        </div>
        <div className="flex items-center space-x-2">
          <div className="h-6 w-16 bg-surface-700/60 rounded-md" />
          <div className="h-6 w-8 bg-surface-700/60 rounded-md" />
        </div>
      </div>

      <div className="relative flex-1 flex overflow-hidden">
        {/* Main Graph Canvas Area */}
        <div className="relative flex-1 p-6 flex flex-col justify-between overflow-hidden">
          {/* Canvas Floating Toolbar */}
          {showToolbar && (
            <div className="absolute top-4 left-4 z-10 flex items-center space-x-2 p-1.5 rounded-lg bg-surface-800/80 border border-surface-700/60 shadow-lg animate-pulse">
              <div className="w-7 h-7 rounded bg-surface-700" />
              <div className="w-7 h-7 rounded bg-surface-700" />
              <div className="w-px h-5 bg-surface-700" />
              <div className="w-7 h-7 rounded bg-surface-700" />
              <div className="w-7 h-7 rounded bg-surface-700" />
              <div className="w-16 h-7 rounded bg-surface-700" />
            </div>
          )}

          {/* Status badge in canvas top-right */}
          <div className="absolute top-4 right-4 z-10 flex items-center space-x-2 px-3 py-1 rounded-full bg-surface-800/80 border border-surface-700/60 animate-pulse">
            <span className="w-2 h-2 rounded-full bg-brand-500 animate-ping" />
            <span className="h-3 w-20 bg-surface-700 rounded text-[11px] text-surface-400">Loading graph...</span>
          </div>

          {/* Canvas SVG connection mock lines */}
          <svg
            className="absolute inset-0 w-full h-full pointer-events-none opacity-30 stroke-surface-600 stroke-dasharray-4 animate-pulse"
            xmlns="http://www.w3.org/2000/svg"
          >
            {variant === 'lineage' ? (
              // Radial / Neo4j Lineage connection lines
              <>
                <line x1="20%" y1="50%" x2="50%" y2="50%" strokeWidth="2" strokeDasharray="4 4" />
                <line x1="50%" y1="50%" x2="80%" y2="30%" strokeWidth="2" strokeDasharray="4 4" />
                <line x1="50%" y1="50%" x2="80%" y2="70%" strokeWidth="2" strokeDasharray="4 4" />
                <line x1="20%" y1="50%" x2="35%" y2="25%" strokeWidth="2" strokeDasharray="4 4" />
                <line x1="20%" y1="50%" x2="35%" y2="75%" strokeWidth="2" strokeDasharray="4 4" />
              </>
            ) : variant === 'pipeline' ? (
              // LangGraph Pipeline sequential workflow transitions
              <>
                <line x1="15%" y1="50%" x2="38%" y2="50%" strokeWidth="2" strokeDasharray="6 3" />
                <line x1="38%" y1="50%" x2="62%" y2="50%" strokeWidth="2" strokeDasharray="6 3" />
                <line x1="62%" y1="50%" x2="85%" y2="50%" strokeWidth="2" strokeDasharray="6 3" />
                <path d="M 38 50 C 50 20, 50 20, 62 50" fill="none" strokeWidth="2" strokeDasharray="4 4" />
              </>
            ) : (
              // Topology / Default mesh
              <>
                <line x1="18%" y1="35%" x2="48%" y2="35%" strokeWidth="2" strokeDasharray="4 4" />
                <line x1="48%" y1="35%" x2="78%" y2="35%" strokeWidth="2" strokeDasharray="4 4" />
                <line x1="48%" y1="35%" x2="48%" y2="70%" strokeWidth="2" strokeDasharray="4 4" />
                <line x1="18%" y1="35%" x2="48%" y2="70%" strokeWidth="2" strokeDasharray="4 4" />
                <line x1="48%" y1="70%" x2="78%" y2="35%" strokeWidth="2" strokeDasharray="4 4" />
              </>
            )}
          </svg>

          {/* Node Placement Variants */}
          {variant === 'pipeline' && (
            <PipelineSkeletonNodes nodesCount={nodesCount} />
          )}

          {variant === 'lineage' && (
            <LineageSkeletonNodes nodesCount={nodesCount} />
          )}

          {(variant === 'topology' || variant === 'default') && (
            <TopologySkeletonNodes nodesCount={nodesCount} />
          )}

          {/* Minimap preview skeleton */}
          <div className="absolute bottom-4 right-4 w-28 h-20 rounded-lg border border-surface-700/60 bg-surface-950/80 p-2 hidden sm:flex flex-col justify-between animate-pulse">
            <div className="flex justify-between">
              <div className="w-3 h-3 rounded-full bg-surface-700" />
              <div className="w-3 h-3 rounded-full bg-surface-700" />
            </div>
            <div className="self-center w-4 h-4 rounded-full bg-brand-500/40" />
            <div className="flex justify-between">
              <div className="w-3 h-3 rounded-full bg-surface-700" />
              <div className="w-3 h-3 rounded-full bg-surface-700" />
            </div>
          </div>
        </div>

        {/* Optional Metadata Inspector Sidebar */}
        {showSidebar && (
          <div className="w-72 border-l border-surface-700/50 bg-surface-850/40 p-4 space-y-4 animate-pulse hidden md:block">
            <div className="h-5 w-28 bg-surface-700 rounded" />
            <div className="h-3 w-44 bg-surface-700/60 rounded" />
            <div className="pt-2 border-t border-surface-700/40 space-y-3">
              <div className="h-4 w-20 bg-surface-700 rounded" />
              <div className="h-16 w-full bg-surface-800 rounded-lg border border-surface-700/40" />
              <div className="h-4 w-24 bg-surface-700 rounded" />
              <div className="space-y-2">
                <div className="h-3 w-full bg-surface-800 rounded" />
                <div className="h-3 w-4/5 bg-surface-800 rounded" />
                <div className="h-3 w-2/3 bg-surface-800 rounded" />
              </div>
            </div>
          </div>
        )}
      </div>

      <span className="sr-only">Graph data is loading</span>
    </div>
  )
}

/**
 * PipelineSkeletonNodes
 * Represents sequential LangGraph state execution nodes with artifact badges.
 */
function PipelineSkeletonNodes({ nodesCount = 4 }: { nodesCount?: number }) {
  const count = Math.max(3, Math.min(nodesCount, 6))
  return (
    <div className="relative z-10 w-full h-full flex items-center justify-around px-4">
      {Array.from({ length: count }).map((_, i) => (
        <div
          key={i}
          className="flex flex-col items-center space-y-3 animate-pulse"
        >
          {/* Node Container Card */}
          <div className="w-44 p-3.5 rounded-xl border border-surface-700/80 bg-surface-800/90 shadow-md space-y-2.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <div className="w-3 h-3 rounded-full bg-brand-500/60" />
                <div className="h-3.5 w-16 bg-surface-600 rounded" />
              </div>
              <div className="h-3 w-8 bg-surface-700 rounded-full" />
            </div>
            <div className="h-2.5 w-full bg-surface-700/60 rounded" />
            <div className="flex items-center justify-between pt-1">
              <div className="h-2 w-10 bg-surface-700/40 rounded" />
              <div className="h-4 w-12 bg-surface-700/60 rounded" />
            </div>
          </div>
          {/* Node Sub-status Pill */}
          <div className="h-2 w-12 bg-surface-700/50 rounded-full" />
        </div>
      ))}
    </div>
  )
}

/**
 * LineageSkeletonNodes
 * Represents Neo4j entity and relationship lineage clusters.
 */
function LineageSkeletonNodes({ nodesCount = 5 }: { nodesCount?: number }) {
  return (
    <div className="relative z-10 w-full h-full flex items-center justify-center animate-pulse">
      {/* Root Node */}
      <div className="absolute left-[15%] top-[45%] flex flex-col items-center space-y-1">
        <div className="w-14 h-14 rounded-2xl bg-brand-600/30 border border-brand-500/50 flex items-center justify-center">
          <div className="w-8 h-8 rounded-xl bg-brand-500/40" />
        </div>
        <div className="h-3 w-16 bg-surface-700 rounded mt-1" />
      </div>

      {/* Central Connector Node */}
      <div className="absolute left-[45%] top-[45%] flex flex-col items-center space-y-1">
        <div className="w-16 h-16 rounded-full bg-surface-750 border-2 border-surface-600 flex items-center justify-center">
          <div className="w-10 h-10 rounded-full bg-surface-700" />
        </div>
        <div className="h-3 w-20 bg-surface-700 rounded mt-1" />
      </div>

      {/* Target Branch Nodes */}
      <div className="absolute right-[18%] top-[25%] flex flex-col items-center space-y-1">
        <div className="w-12 h-12 rounded-xl bg-surface-800 border border-surface-700 flex items-center justify-center">
          <div className="w-6 h-6 rounded bg-surface-700" />
        </div>
        <div className="h-2.5 w-14 bg-surface-700 rounded" />
      </div>

      <div className="absolute right-[18%] top-[65%] flex flex-col items-center space-y-1">
        <div className="w-12 h-12 rounded-xl bg-surface-800 border border-surface-700 flex items-center justify-center">
          <div className="w-6 h-6 rounded bg-surface-700" />
        </div>
        <div className="h-2.5 w-14 bg-surface-700 rounded" />
      </div>
    </div>
  )
}

/**
 * TopologySkeletonNodes
 * Represents generic DAG and workflow node networks.
 */
function TopologySkeletonNodes({ nodesCount = 6 }: { nodesCount?: number }) {
  return (
    <div className="relative z-10 w-full h-full p-8 flex flex-col justify-between animate-pulse">
      <div className="flex justify-around items-center">
        <div className="w-36 h-20 rounded-xl bg-surface-800/80 border border-surface-700/60 p-3 space-y-2">
          <div className="h-3 w-20 bg-surface-700 rounded" />
          <div className="h-2.5 w-28 bg-surface-700/60 rounded" />
        </div>
        <div className="w-36 h-20 rounded-xl bg-surface-800/80 border border-surface-700/60 p-3 space-y-2">
          <div className="h-3 w-20 bg-surface-700 rounded" />
          <div className="h-2.5 w-28 bg-surface-700/60 rounded" />
        </div>
      </div>

      <div className="flex justify-center items-center">
        <div className="w-40 h-24 rounded-xl bg-surface-800/90 border-2 border-surface-650 p-3 space-y-2 shadow-lg">
          <div className="h-3.5 w-24 bg-brand-500/40 rounded" />
          <div className="h-2.5 w-32 bg-surface-700/60 rounded" />
          <div className="h-2 w-16 bg-surface-700/40 rounded" />
        </div>
      </div>

      <div className="flex justify-around items-center">
        <div className="w-36 h-20 rounded-xl bg-surface-800/80 border border-surface-700/60 p-3 space-y-2">
          <div className="h-3 w-20 bg-surface-700 rounded" />
          <div className="h-2.5 w-28 bg-surface-700/60 rounded" />
        </div>
      </div>
    </div>
  )
}

export default GraphSkeleton
