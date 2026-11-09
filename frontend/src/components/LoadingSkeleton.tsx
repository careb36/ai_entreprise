import React from 'react';
import {
  SkeletonBase,
  SkeletonText,
  SkeletonCard,
  SkeletonTable,
  SkeletonGraph,
} from './skeleton';

interface LoadingSkeletonProps {
  variant?: 'card' | 'table' | 'text' | 'graph' | 'avatar' | 'rect';
  count?: number;
  className?: string;
  rows?: number;
  cols?: number;
  height?: string | number;
  width?: string | number;
}

/**
 * Standardized Loading Skeleton component adhering to Doherty Threshold (<400ms UX feedback).
 * Wraps modular primitives from the standardized skeleton library.
 */
export const LoadingSkeleton: React.FC<LoadingSkeletonProps> = ({
  variant = 'card',
  count = 1,
  className = '',
  rows = 5,
  cols = 4,
  height,
  width,
}) => {
  const items = Array.from({ length: count });

  switch (variant) {
    case 'avatar':
      return (
        <div className={`flex gap-3 items-center ${className}`}>
          {items.map((_, i) => (
            <SkeletonBase
              key={i}
              variant="circular"
              width={width || '2.5rem'}
              height={height || '2.5rem'}
            />
          ))}
        </div>
      );

    case 'rect':
      return (
        <div className={`space-y-3 ${className}`}>
          {items.map((_, i) => (
            <SkeletonBase
              key={i}
              variant="rectangular"
              width={width || '100%'}
              height={height || '6rem'}
            />
          ))}
        </div>
      );

    case 'text':
      return (
        <div className={`space-y-4 ${className}`}>
          {items.map((_, i) => (
            <SkeletonText
              key={i}
              lines={rows}
              lineHeight={height || '1rem'}
            />
          ))}
        </div>
      );

    case 'table':
      return (
        <div className={`space-y-4 ${className}`}>
          {items.map((_, i) => (
            <SkeletonTable
              key={i}
              rows={rows}
              columns={cols}
            />
          ))}
        </div>
      );

    case 'graph':
      return (
        <div className={`space-y-4 ${className}`}>
          {items.map((_, i) => (
            <SkeletonGraph
              key={i}
              height={height || '18rem'}
            />
          ))}
        </div>
      );

    case 'card':
    default:
      return (
        <div className={`grid gap-4 ${count > 1 ? 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3' : 'grid-cols-1'} ${className}`}>
          {items.map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      );
  }
};

export default LoadingSkeleton;
