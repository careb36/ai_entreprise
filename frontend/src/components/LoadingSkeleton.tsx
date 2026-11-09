/**
 * LoadingSkeleton — reusable loading placeholder library.
 * Fully supports Doherty Threshold (<400ms) with lightweight animated skeletons
 * instead of blocking spinners.
 */

import React from 'react'
import { GraphSkeleton, GraphSkeletonProps } from './GraphSkeleton'

interface LoadingSkeletonProps {
  message?: string
  className?: string
  /**
   * If true, renders a structural card skeleton instead of a simple pulsing block.
   */
  variant?: 'block' | 'card' | 'graph' | 'table'
}

export function LoadingSkeleton({
  message,
  className = '',
  variant = 'block',
}: LoadingSkeletonProps) {
  if (variant === 'graph') {
    return <GraphSkeleton className={className} />
  }

  if (variant === 'card') {
    return <CardSkeleton />
  }

  if (variant === 'table') {
    return <TableSkeleton />
  }

  return (
    <div
      role="status"
      aria-busy="true"
      aria-label={message || 'Loading content'}
      className={`w-full p-6 flex flex-col items-center justify-center space-y-3 ${className}`}
    >
      <div className="w-full max-w-md space-y-2.5 animate-pulse">
        <div className="h-4 bg-surface-700 rounded-md w-3/4 mx-auto" />
        <div className="h-3 bg-surface-800 rounded w-1/2 mx-auto" />
      </div>
      {message && <p className="text-xs text-surface-500 font-mono tracking-wide">{message}</p>}
      <span className="sr-only">{message || 'Loading...'}</span>
    </div>
  )
}

export function CardSkeleton() {
  return (
    <div
      role="status"
      aria-busy="true"
      aria-label="Loading card"
      className="rounded-xl border border-surface-700 bg-surface-900 p-4 animate-pulse space-y-3"
    >
      <div className="h-4 bg-surface-700 rounded w-1/3" />
      <div className="h-3 bg-surface-800 rounded w-2/3" />
      <div className="h-3 bg-surface-800/80 rounded w-1/2" />
    </div>
  )
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div role="status" aria-busy="true" aria-label="Loading table" className="space-y-2 w-full">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-10 bg-surface-800 rounded-lg animate-pulse" />
      ))}
    </div>
  )
}

export { GraphSkeleton }
export type { GraphSkeletonProps }
