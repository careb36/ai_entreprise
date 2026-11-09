import { HTMLAttributes } from 'react';

export type SkeletonVariant = 'text' | 'circular' | 'rectangular';
export type SkeletonAnimation = 'pulse' | 'wave' | 'none';

export interface BaseSkeletonProps extends HTMLAttributes<HTMLDivElement> {
  variant?: SkeletonVariant;
  animation?: SkeletonAnimation;
  width?: string | number;
  height?: string | number;
  borderRadius?: string;
  className?: string;
  'aria-label'?: string;
}

export interface SkeletonTextProps extends Omit<BaseSkeletonProps, 'variant'> {
  lines?: number;
  lineHeight?: string | number;
  lastLineWidth?: string;
  gap?: string | number;
}

export interface SkeletonCardProps {
  className?: string;
  hasAvatar?: boolean;
  avatarSize?: string;
  lines?: number;
  hasFooter?: boolean;
}

export interface SkeletonTableProps {
  rows?: number;
  columns?: number;
  className?: string;
  hasHeader?: boolean;
}

export interface SkeletonGraphProps {
  nodes?: number;
  height?: string | number;
  className?: string;
  layout?: 'network' | 'bar' | 'timeline';
}
