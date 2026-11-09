import React from 'react';

export interface LoadingSkeletonProps {
  className?: string;
  count?: number;
  variant?: 'text' | 'card' | 'circle' | 'table-row';
  height?: string | number;
  width?: string | number;
}

export const LoadingSkeleton: React.FC<LoadingSkeletonProps> = ({
  className = '',
  count = 1,
  variant = 'text',
  height,
  width,
}) => {
  const getVariantStyles = () => {
    switch (variant) {
      case 'circle':
        return 'rounded-full w-10 h-10';
      case 'card':
        return 'rounded-xl h-36 w-full p-4 border border-slate-800';
      case 'table-row':
        return 'rounded-md h-10 w-full';
      case 'text':
      default:
        return 'rounded h-4 w-full';
    }
  };

  const style: React.CSSProperties = {
    ...(height ? { height: typeof height === 'number' ? `${height}px` : height } : {}),
    ...(width ? { width: typeof width === 'number' ? `${width}px` : width } : {}),
  };

  const items = Array.from({ length: count }, (_, idx) => (
    <div
      key={idx}
      role="status"
      aria-busy="true"
      style={style}
      className={`bg-slate-800/70 animate-pulse relative overflow-hidden ${getVariantStyles()} ${className}`}
      data-testid="loading-skeleton-item"
    >
      <div className="absolute inset-0 -translate-x-full animate-[shimmer_2s_infinite] bg-gradient-to-r from-transparent via-slate-700/20 to-transparent pointer-events-none" />
    </div>
  ));

  return count === 1 ? items[0] : <div className="space-y-2 w-full">{items}</div>;
};

export default LoadingSkeleton;
