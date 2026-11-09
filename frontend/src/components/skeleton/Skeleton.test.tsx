import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import {
  SkeletonBase,
  SkeletonText,
  SkeletonCard,
  SkeletonTable,
  SkeletonGraph,
} from './index';

describe('Standardized Skeleton Loader Component Library', () => {
  it('renders SkeletonBase with proper accessible attributes', () => {
    render(<SkeletonBase width={120} height={40} aria-label="Loading widget" />);
    const el = screen.getByRole('status');
    expect(el).toBeInTheDocument();
    expect(el).toHaveAttribute('aria-busy', 'true');
    expect(el).toHaveAttribute('aria-label', 'Loading widget');
    expect(el).toHaveStyle({ width: '120px', height: '40px' });
  });

  it('renders SkeletonBase with circular variant and wave animation', () => {
    render(<SkeletonBase variant="circular" animation="wave" width="48px" height="48px" />);
    const el = screen.getByTestId('skeleton-base');
    expect(el.className).toContain('rounded-full');
    expect(el.className).toContain('overflow-hidden');
  });

  it('renders SkeletonText with expected number of lines', () => {
    const { container } = render(<SkeletonText lines={4} lastLineWidth="50%" />);
    const lines = container.querySelectorAll('[data-testid="skeleton-base"]');
    expect(lines.length).toBe(4);
    expect(lines[3]).toHaveStyle({ width: '50%' });
  });

  it('renders SkeletonCard containing avatar and text lines', () => {
    render(<SkeletonCard hasAvatar={true} lines={2} />);
    const card = screen.getByTestId('skeleton-card');
    expect(card).toBeInTheDocument();
  });

  it('renders SkeletonTable with specified rows and columns', () => {
    render(<SkeletonTable rows={3} columns={5} hasHeader={true} />);
    const table = screen.getByTestId('skeleton-table');
    expect(table).toBeInTheDocument();
    // 5 header cells + (3 rows * 5 cols = 15) = 20 skeleton cells
    const cells = table.querySelectorAll('[data-testid="skeleton-base"]');
    expect(cells.length).toBe(20);
  });

  it('renders SkeletonGraph in both network and bar layouts', () => {
    const { rerender } = render(<SkeletonGraph layout="network" nodes={4} />);
    expect(screen.getByTestId('skeleton-graph')).toBeInTheDocument();

    rerender(<SkeletonGraph layout="bar" nodes={5} />);
    expect(screen.getByTestId('skeleton-graph')).toBeInTheDocument();
  });
});
