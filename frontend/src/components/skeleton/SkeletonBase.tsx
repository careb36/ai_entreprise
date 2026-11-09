import React from 'react';
import { BaseSkeletonProps } from './types';

export const SkeletonBase: React.FC<BaseSkeletonProps> = ({
  variant = 'text',
  animation = 'pulse',
  width,
  height,
  borderRadius,
  className = '',
  style,
  'aria-label': ariaLabel = 'Loading content...',
  ...rest
}) => {
  const getVariantClasses = () => {
    switch (variant) {
      case 'circular':
        return 'rounded-full';
      case 'rectangular':
        return 'rounded-md';
      case 'text':
      default:
        return 'rounded';
    }
  };

  const getAnimationClasses = () => {
    switch (animation) {
      case 'wave':
        return 'relative overflow-hidden before:absolute before:inset-0 before:-translate-x-full before:animate-[shimmer_2s_infinite] before:bg-gradient-to-r before:from-transparent before:via-white/10 before:to-transparent';
      case 'none':
        return '';
      case 'pulse':
      default:
        return 'animate-pulse';
    }
  };

  const computedStyle: React.CSSProperties = {
    width: typeof width === 'number' ? `${width}px` : width,
    height: typeof height === 'number' ? `${height}px` : height,
    borderRadius: borderRadius,
    ...style,
  };

  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label={ariaLabel}
      className={`bg-slate-700/50 dark:bg-slate-800/60 ${getVariantClasses()} ${getAnimationClasses()} ${className}`.trim()}
      style={computedStyle}
      data-testid="skeleton-base"
      {...rest}
    >
      <span className="sr-only">{ariaLabel}</span>
    </div>
  );
};
