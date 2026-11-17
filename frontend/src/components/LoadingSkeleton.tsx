/**
 * LoadingSkeleton — reusable loading placeholders in the panel look. Every
 * animation stops for a person who asked for reduced motion.
 */

import { Loader2 } from 'lucide-react'

interface LoadingSkeletonProps {
  message?: string
  className?: string
}

export function LoadingSkeleton({ message, className = '' }: LoadingSkeletonProps) {
  return (
    <div role="status" className={`empty-state ${className}`}>
      <Loader2 size={22} aria-hidden="true" className="animate-spin text-brand-400 motion-reduce:animate-none" />
      {message && <p className="text-[13px] text-gray-400">{message}</p>}
    </div>
  )
}

export function CardSkeleton() {
  return (
    <div aria-hidden="true" className="panel animate-pulse p-4 motion-reduce:animate-none">
      <div className="mb-3 h-4 w-1/3 rounded bg-surface-700" />
      <div className="mb-2 h-3 w-2/3 rounded bg-surface-700" />
      <div className="h-3 w-1/2 rounded bg-surface-700" />
    </div>
  )
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div aria-hidden="true" className="panel data-list overflow-hidden">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="px-3 py-2.5">
          <div className="h-5 animate-pulse rounded bg-surface-800 motion-reduce:animate-none" />
        </div>
      ))}
    </div>
  )
}
