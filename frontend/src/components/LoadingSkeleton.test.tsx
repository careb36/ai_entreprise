import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { LoadingSkeleton } from './LoadingSkeleton';

describe('LoadingSkeleton Adapter Component', () => {
  it('renders default card skeletons with accessibility attributes', () => {
    render(<LoadingSkeleton count={2} />);
    const statuses = screen.getAllByRole('status');
    expect(statuses.length).toBeGreaterThanOrEqual(2);
  });

  it('renders table variant with specified rows and columns', () => {
    render(<LoadingSkeleton variant="table" rows={4} cols={3} />);
    const table = screen.getByTestId('skeleton-table');
    expect(table).toBeInTheDocument();
  });

  it('renders text variant with specified rows count', () => {
    render(<LoadingSkeleton variant="text" rows={5} />);
    const status = screen.getByLabelText('Loading text content');
    expect(status).toBeInTheDocument();
  });

  it('renders graph variant seamlessly', () => {
    render(<LoadingSkeleton variant="graph" height="200px" />);
    const graph = screen.getByTestId('skeleton-graph');
    expect(graph).toBeInTheDocument();
  });

  it('renders circular avatar variant', () => {
    render(<LoadingSkeleton variant="avatar" count={3} />);
    const bases = screen.getAllByTestId('skeleton-base');
    expect(bases.length).toBe(3);
    expect(bases[0].className).toContain('rounded-full');
  });
});
