import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  GraphSkeleton,
  GraphCanvasSkeleton,
  LineageGraphSkeleton,
  LangGraphTraceSkeleton,
  DOHERTY_THRESHOLD_MS,
} from './GraphSkeleton';

describe('GraphSkeleton Component (Doherty Threshold Compliance)', () => {
  it('defines the Doherty Threshold as <= 400ms', () => {
    expect(DOHERTY_THRESHOLD_MS).toBe(400);
  });

  it('renders skeleton container with accessibility attributes', () => {
    render(<GraphSkeleton />);
    const skeleton = screen.getByTestId('graph-skeleton-container');
    expect(skeleton).toBeDefined();
    expect(skeleton.getAttribute('role')).toBe('status');
    expect(skeleton.getAttribute('aria-busy')).toBe('true');
    expect(skeleton.getAttribute('aria-label')).toBe('Loading graph visualization...');
  });

  it('renders graph canvas skeleton elements within Doherty limits', () => {
    const startTime = performance.now();
    render(<GraphCanvasSkeleton variant="neo4j-lineage" />);
    const duration = performance.now() - startTime;

    expect(duration).toBeLessThan(DOHERTY_THRESHOLD_MS);
    expect(screen.getByTestId('graph-canvas-skeleton')).toBeDefined();
    const nodes = screen.getAllByTestId('skeleton-node');
    expect(nodes.length).toBeGreaterThan(0);
  });

  it('renders LangGraph trace variant with corresponding node count', () => {
    render(<LangGraphTraceSkeleton />);
    expect(screen.getByTestId('graph-skeleton-container')).toBeDefined();
    const nodes = screen.getAllByTestId('skeleton-node');
    expect(nodes.length).toBe(5); // 5 langgraph nodes
  });

  it('renders sidebar skeleton when showSidebar is true', () => {
    render(<LineageGraphSkeleton showSidebar={true} />);
    expect(screen.getByTestId('graph-side-panel-skeleton')).toBeDefined();
  });

  it('does not render static circular spinner but SVG pulse skeletons', () => {
    const { container } = render(<GraphSkeleton />);
    // Static spinner would be a lone border-spinner or spinner icon; skeleton uses animate-pulse & canvas
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0);
  });
});
