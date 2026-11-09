import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

/**
 * Doherty Threshold Constant
 * In human-computer interaction, computer processing response time <= 400ms
 * maintains user engagement and prevents task-switching or perceived lag.
 */
export const DOHERTY_THRESHOLD_MS = 400;

export interface GraphSkeletonProps {
  variant?: 'neo4j-lineage' | 'langgraph-trace' | 'dependency-tree' | 'canvas';
  nodeCount?: number;
  className?: string;
  showToolbar?: boolean;
  showSidebar?: boolean;
  showMiniMap?: boolean;
}

/**
 * Hook to measure rendering latency and verify Doherty compliance (<400ms perceived delay)
 */
export const useDohertyMetrics = (componentName: string = 'GraphSkeleton') => {
  const [mountTime] = useState<number>(() => (typeof performance !== 'undefined' ? performance.now() : Date.now()));
  const [renderedUnderDoherty, setRenderedUnderDoherty] = useState<boolean>(true);

  useEffect(() => {
    const elapsed = (typeof performance !== 'undefined' ? performance.now() : Date.now()) - mountTime;
    if (elapsed > DOHERTY_THRESHOLD_MS) {
      setRenderedUnderDoherty(false);
      console.warn(`[UX Warning] ${componentName} exceeded Doherty threshold: ${elapsed.toFixed(1)}ms`);
    }
  }, [componentName, mountTime]);

  return { renderedUnderDoherty };
};

/**
 * SVG-based simulated Graph Canvas Skeleton for LangGraph / Neo4j Graph Views
 */
export const GraphCanvasSkeleton: React.FC<{ variant?: GraphSkeletonProps['variant']; className?: string }> = ({
  variant = 'neo4j-lineage',
  className = '',
}) => {
  const { t } = useTranslation();
  return (
    <div
      className={`relative w-full h-[520px] bg-slate-900/60 rounded-xl border border-slate-800/80 overflow-hidden flex items-center justify-center ${className}`}
      data-testid="graph-canvas-skeleton"
    >
      {/* Background Grid Pattern Skeleton */}
      <svg
        className="absolute inset-0 w-full h-full stroke-slate-800/30"
        xmlns="http://www.w3.org/2000/svg"
        fill="none"
      >
        <defs>
          <pattern id="grid-pattern-skeleton" width="32" height="32" patternUnits="userSpaceOnUse">
            <path d="M 32 0 L 0 0 0 32" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid-pattern-skeleton)" />
      </svg>

      {/* Shimmer overlay gradient */}
      <div className="absolute inset-0 -translate-x-full animate-[shimmer_2s_infinite] bg-gradient-to-r from-transparent via-slate-700/10 to-transparent pointer-events-none" />

      {/* Graph Node & Edge Simulation */}
      <svg
        className="w-full h-full max-w-4xl max-h-[480px] p-6 animate-pulse"
        viewBox="0 0 800 480"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        {/* Graph Edges / Connectors */}
        <g className="stroke-slate-700/60 stroke-2" strokeDasharray="4 4">
          {variant === 'langgraph-trace' ? (
            <>
              {/* Linear / Conditional LangGraph pipeline edges */}
              <path d="M 160 240 L 280 240" />
              <path d="M 360 240 C 400 240, 420 160, 460 160" />
              <path d="M 360 240 C 400 240, 420 320, 460 320" />
              <path d="M 540 160 C 580 160, 600 240, 640 240" />
              <path d="M 540 320 C 580 320, 600 240, 640 240" />
            </>
          ) : (
            <>
              {/* Neo4j Entity Lineage & Evidence Graph edges */}
              <line x1="200" y1="160" x2="380" y2="240" />
              <line x1="200" y1="320" x2="380" y2="240" />
              <line x1="380" y1="240" x2="560" y2="180" />
              <line x1="380" y1="240" x2="560" y2="300" />
              <line x1="560" y1="180" x2="680" y2="220" />
              <line x1="560" y1="300" x2="680" y2="220" />
            </>
          )}
        </g>

        {/* Graph Nodes */}
        {variant === 'langgraph-trace' ? (
          <>
            {/* LangGraph Node 1: Start / Router */}
            <g transform="translate(100, 210)" data-testid="skeleton-node">
              <rect width="60" height="60" rx="30" className="fill-slate-800 stroke-slate-700 stroke-2" />
              <circle cx="30" cy="30" r="16" className="fill-cyan-900/40" />
            </g>

            {/* LangGraph Node 2: Agent Dispatch */}
            <g transform="translate(280, 210)" data-testid="skeleton-node">
              <rect width="80" height="60" rx="12" className="fill-slate-800 stroke-slate-700 stroke-2" />
              <rect x="12" y="22" width="56" height="8" rx="4" className="fill-slate-700" />
              <rect x="18" y="36" width="44" height="6" rx="3" className="fill-slate-700/60" />
            </g>

            {/* LangGraph Node 3a: Verification Node */}
            <g transform="translate(460, 130)" data-testid="skeleton-node">
              <rect width="80" height="60" rx="12" className="fill-slate-800 stroke-slate-700 stroke-2" />
              <rect x="12" y="22" width="56" height="8" rx="4" className="fill-slate-700" />
              <rect x="20" y="36" width="40" height="6" rx="3" className="fill-slate-700/60" />
            </g>

            {/* LangGraph Node 3b: Tool Execution Node */}
            <g transform="translate(460, 290)" data-testid="skeleton-node">
              <rect width="80" height="60" rx="12" className="fill-slate-800 stroke-slate-700 stroke-2" />
              <rect x="12" y="22" width="56" height="8" rx="4" className="fill-slate-700" />
              <rect x="24" y="36" width="32" height="6" rx="3" className="fill-slate-700/60" />
            </g>

            {/* LangGraph Node 4: Evidence Output / End */}
            <g transform="translate(640, 210)" data-testid="skeleton-node">
              <rect width="60" height="60" rx="30" className="fill-slate-800 stroke-slate-700 stroke-2" />
              <circle cx="30" cy="30" r="16" className="fill-emerald-900/40" />
            </g>
          </>
        ) : (
          <>
            {/* Neo4j Node 1: Code Artifact */}
            <g transform="translate(160, 130)" data-testid="skeleton-node">
              <circle cx="40" cy="30" r="28" className="fill-slate-800 stroke-cyan-500/30 stroke-2" />
              <rect x="15" y="66" width="50" height="8" rx="4" className="fill-slate-700" />
            </g>

            {/* Neo4j Node 2: Test Suite Evidence */}
            <g transform="translate(160, 290)" data-testid="skeleton-node">
              <circle cx="40" cy="30" r="28" className="fill-slate-800 stroke-indigo-500/30 stroke-2" />
              <rect x="15" y="66" width="50" height="8" rx="4" className="fill-slate-700" />
            </g>

            {/* Neo4j Node 3: Core Hub Node (Central entity) */}
            <g transform="translate(340, 200)" data-testid="skeleton-node">
              <circle cx="40" cy="40" r="38" className="fill-slate-800 stroke-violet-500/40 stroke-2" />
              <circle cx="40" cy="40" r="20" className="fill-violet-950/50" />
              <rect x="10" y="86" width="60" height="10" rx="4" className="fill-slate-700" />
            </g>

            {/* Neo4j Node 4: Policy / Gate Node */}
            <g transform="translate(520, 140)" data-testid="skeleton-node">
              <circle cx="40" cy="40" r="28" className="fill-slate-800 stroke-amber-500/30 stroke-2" />
              <rect x="15" y="74" width="50" height="8" rx="4" className="fill-slate-700" />
            </g>

            {/* Neo4j Node 5: Verification Node */}
            <g transform="translate(520, 260)" data-testid="skeleton-node">
              <circle cx="40" cy="40" r="28" className="fill-slate-800 stroke-emerald-500/30 stroke-2" />
              <rect x="15" y="74" width="50" height="8" rx="4" className="fill-slate-700" />
            </g>

            {/* Neo4j Node 6: Release Observation Target */}
            <g transform="translate(640, 185)" data-testid="skeleton-node">
              <circle cx="40" cy="35" r="32" className="fill-slate-800 stroke-cyan-500/30 stroke-2" />
              <rect x="15" y="74" width="50" height="8" rx="4" className="fill-slate-700" />
            </g>
          </>
        )}
      </svg>

      {/* Doherty Status Badge */}
      <div className="absolute bottom-3 left-4 flex items-center space-x-2 text-xs text-slate-400 font-mono select-none">
        <span className="w-2 h-2 rounded-full bg-cyan-400 animate-ping" />
        <span>{t('graphSkeleton.loadingTopology')}</span>
      </div>
    </div>
  );
};

/**
 * Top Toolbar Skeleton for Graph views (Zoom, Reset, Layout selector)
 */
export const GraphToolbarSkeleton: React.FC = () => (
  <div className="h-12 w-full flex items-center justify-between px-4 bg-slate-900/70 border border-slate-800 rounded-lg mb-3">
    <div className="flex items-center space-x-2">
      <div className="w-24 h-6 bg-slate-800 rounded animate-pulse" />
      <div className="w-16 h-6 bg-slate-800/60 rounded animate-pulse" />
    </div>
    <div className="flex items-center space-x-2">
      <div className="w-8 h-8 bg-slate-800 rounded-lg animate-pulse" />
      <div className="w-8 h-8 bg-slate-800 rounded-lg animate-pulse" />
      <div className="w-8 h-8 bg-slate-800 rounded-lg animate-pulse" />
      <div className="h-6 w-px bg-slate-800 mx-1" />
      <div className="w-28 h-8 bg-slate-800 rounded-lg animate-pulse" />
    </div>
  </div>
);

/**
 * Side Details Panel Skeleton for Graph entity inspection
 */
export const GraphSidePanelSkeleton: React.FC = () => (
  <div
    className="w-80 h-[520px] bg-slate-900/60 border border-slate-800 rounded-xl p-4 flex flex-col space-y-4 animate-pulse"
    data-testid="graph-side-panel-skeleton"
  >
    <div className="flex items-center space-x-3 pb-3 border-b border-slate-800">
      <div className="w-10 h-10 rounded-full bg-slate-800" />
      <div className="space-y-1.5 flex-1">
        <div className="h-4 w-3/4 bg-slate-800 rounded" />
        <div className="h-3 w-1/2 bg-slate-800/60 rounded" />
      </div>
    </div>

    <div className="space-y-3 flex-1">
      <div className="h-3 w-1/3 bg-slate-800/80 rounded" />
      <div className="h-8 w-full bg-slate-800/40 rounded-lg" />
      <div className="h-8 w-full bg-slate-800/40 rounded-lg" />
      <div className="h-8 w-full bg-slate-800/40 rounded-lg" />

      <div className="pt-2">
        <div className="h-3 w-1/4 bg-slate-800/80 rounded mb-2" />
        <div className="space-y-1.5">
          <div className="h-3 w-full bg-slate-800/40 rounded" />
          <div className="h-3 w-5/6 bg-slate-800/40 rounded" />
          <div className="h-3 w-2/3 bg-slate-800/40 rounded" />
        </div>
      </div>
    </div>

    <div className="pt-3 border-t border-slate-800 flex justify-end space-x-2">
      <div className="h-8 w-20 bg-slate-800 rounded-lg" />
      <div className="h-8 w-24 bg-slate-800 rounded-lg" />
    </div>
  </div>
);

/**
 * Complete Graph View Skeleton Component
 * Compliant with Doherty threshold (<400ms visual response requirement)
 */
export const GraphSkeleton: React.FC<GraphSkeletonProps> = ({
  variant = 'neo4j-lineage',
  className = '',
  showToolbar = true,
  showSidebar = false,
}) => {
  useDohertyMetrics(`GraphSkeleton-${variant}`);
  const { t } = useTranslation();

  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={t('graphSkeleton.loadingVisualization')}
      className={`w-full flex flex-col ${className}`}
      data-testid="graph-skeleton-container"
    >
      {showToolbar && <GraphToolbarSkeleton />}
      <div className="flex space-x-4 w-full">
        <div className="flex-1">
          <GraphCanvasSkeleton variant={variant} />
        </div>
        {showSidebar && <GraphSidePanelSkeleton />}
      </div>
      <span className="sr-only">{t('graphSkeleton.loadingData')}</span>
    </div>
  );
};

export const LineageGraphSkeleton: React.FC<Omit<GraphSkeletonProps, 'variant'>> = (props) => (
  <GraphSkeleton variant="neo4j-lineage" {...props} />
);

export const LangGraphTraceSkeleton: React.FC<Omit<GraphSkeletonProps, 'variant'>> = (props) => (
  <GraphSkeleton variant="langgraph-trace" {...props} />
);

export default GraphSkeleton;
