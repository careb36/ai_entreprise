import React from 'react';
import { SkeletonBase } from './SkeletonBase';
import {
  SkeletonTextProps,
  SkeletonCardProps,
  SkeletonTableProps,
  SkeletonGraphProps,
} from './types';

/**
 * Multi-line paragraph skeleton with varied widths
 */
export const SkeletonText: React.FC<SkeletonTextProps> = ({
  lines = 3,
  lineHeight = '1rem',
  lastLineWidth = '70%',
  gap = '0.5rem',
  className = '',
  animation = 'pulse',
  ...rest
}) => {
  return (
    <div
      className={`w-full flex flex-col ${className}`}
      style={{ gap }}
      role="status"
      aria-label="Loading text content"
    >
      {Array.from({ length: lines }).map((_, index) => {
        const isLast = index === lines - 1;
        const width = isLast ? lastLineWidth : '100%';

        return (
          <SkeletonBase
            key={index}
            variant="text"
            animation={animation}
            height={lineHeight}
            width={width}
            {...rest}
          />
        );
      })}
    </div>
  );
};

/**
 * Standard Card skeleton loader
 */
export const SkeletonCard: React.FC<SkeletonCardProps> = ({
  className = '',
  hasAvatar = true,
  avatarSize = '2.5rem',
  lines = 3,
  hasFooter = true,
}) => {
  return (
    <div
      className={`p-4 rounded-xl border border-slate-700/50 bg-slate-900/40 backdrop-blur-sm space-y-4 ${className}`}
      role="status"
      aria-label="Loading card item"
      data-testid="skeleton-card"
    >
      <div className="flex items-center gap-3">
        {hasAvatar && (
          <SkeletonBase
            variant="circular"
            width={avatarSize}
            height={avatarSize}
            className="shrink-0"
          />
        )}
        <div className="flex-1 space-y-2">
          <SkeletonBase variant="text" height="1rem" width="60%" />
          <SkeletonBase variant="text" height="0.75rem" width="40%" />
        </div>
      </div>

      <div className="space-y-2 pt-2">
        <SkeletonText lines={lines} lineHeight="0.875rem" lastLineWidth="75%" />
      </div>

      {hasFooter && (
        <div className="pt-2 border-t border-slate-800 flex justify-between items-center">
          <SkeletonBase variant="rectangular" height="1.5rem" width="4rem" />
          <SkeletonBase variant="circular" width="1.5rem" height="1.5rem" />
        </div>
      )}
    </div>
  );
};

/**
 * Data Table skeleton loader
 */
export const SkeletonTable: React.FC<SkeletonTableProps> = ({
  rows = 5,
  columns = 4,
  className = '',
  hasHeader = true,
}) => {
  return (
    <div
      className={`w-full border border-slate-700/50 rounded-lg overflow-hidden ${className}`}
      role="status"
      aria-label="Loading table data"
      data-testid="skeleton-table"
    >
      {hasHeader && (
        <div
          className="bg-slate-800/80 px-4 py-3 border-b border-slate-700/60 grid gap-4"
          style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
        >
          {Array.from({ length: columns }).map((_, i) => (
            <SkeletonBase key={i} variant="text" height="1rem" width="70%" />
          ))}
        </div>
      )}
      <div className="divide-y divide-slate-800/60 bg-slate-900/30">
        {Array.from({ length: rows }).map((_, r) => (
          <div
            key={r}
            className="px-4 py-3.5 grid gap-4 items-center"
            style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
          >
            {Array.from({ length: columns }).map((_, c) => (
              <SkeletonBase
                key={c}
                variant="text"
                height="0.875rem"
                width={c === 0 ? '85%' : '60%'}
              />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
};

/**
 * Complex Graph / Chart visualization skeleton
 */
export const SkeletonGraph: React.FC<SkeletonGraphProps> = ({
  nodes = 6,
  height = '18rem',
  className = '',
  layout = 'network',
}) => {
  return (
    <div
      className={`w-full rounded-xl border border-slate-700/50 bg-slate-900/40 p-5 flex flex-col justify-between ${className}`}
      style={{ height }}
      role="status"
      aria-label="Loading graph visualization"
      data-testid="skeleton-graph"
    >
      <div className="flex justify-between items-center border-b border-slate-800 pb-3">
        <SkeletonBase variant="text" width="30%" height="1.25rem" />
        <div className="flex gap-2">
          <SkeletonBase variant="rectangular" width="4rem" height="1.75rem" />
          <SkeletonBase variant="rectangular" width="4rem" height="1.75rem" />
        </div>
      </div>

      {layout === 'bar' ? (
        <div className="flex items-end justify-between gap-3 h-36 px-4 pt-6">
          {Array.from({ length: nodes }).map((_, i) => {
            const barHeights = ['45%', '70%', '30%', '85%', '60%', '95%', '50%'];
            const h = barHeights[i % barHeights.length];
            return (
              <div key={i} className="flex-1 flex flex-col items-center gap-2 h-full justify-end">
                <SkeletonBase variant="rectangular" width="100%" height={h} />
                <SkeletonBase variant="text" width="60%" height="0.625rem" />
              </div>
            );
          })}
        </div>
      ) : (
        <div className="relative flex-1 my-4 flex items-center justify-around">
          {Array.from({ length: nodes }).map((_, i) => {
            const sizes = ['3rem', '2.5rem', '3.5rem', '2.75rem'];
            const size = sizes[i % sizes.length];
            return (
              <div key={i} className="flex flex-col items-center gap-2">
                <SkeletonBase variant="circular" width={size} height={size} />
                <SkeletonBase variant="text" width="2.5rem" height="0.5rem" />
              </div>
            );
          })}
        </div>
      )}

      <div className="flex justify-between items-center pt-3 border-t border-slate-800 text-xs">
        <SkeletonBase variant="text" width="20%" height="0.75rem" />
        <SkeletonBase variant="text" width="15%" height="0.75rem" />
      </div>
    </div>
  );
};
