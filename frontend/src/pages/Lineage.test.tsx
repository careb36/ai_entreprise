import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { Lineage } from './Lineage';

describe('Lineage Page with Doherty Skeleton Integration', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('displays skeleton loader under 400ms during initial data fetch', async () => {
    // Simulate delayed fetch
    global.fetch = vi.fn().mockImplementation(
      () =>
        new Promise((resolve) =>
          setTimeout(
            () =>
              resolve({
                ok: true,
                json: async () => ({
                  nodes: [
                    { id: '1', name: 'Artifact A', type: 'code_artifact', status: 'passed', timestamp: '12:00' },
                  ],
                  edges: [],
                }),
              }),
            150
          )
        )
    );

    const startTime = performance.now();
    render(<Lineage />);
    const renderTime = performance.now() - startTime;

    // Immediately rendered skeleton (well under 400ms Doherty threshold)
    expect(renderTime).toBeLessThan(400);
    expect(screen.getByTestId('lineage-skeleton-loader')).toBeDefined();
    expect(screen.getByTestId('graph-skeleton-container')).toBeDefined();

    // After resolution, skeleton disappears and loaded graph appears
    await waitFor(() => {
      expect(screen.getByTestId('lineage-graph-container')).toBeDefined();
    });

    expect(screen.queryByTestId('lineage-skeleton-loader')).toBeNull();
  });

  it('renders node inspector when graph data is resolved', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        nodes: [
          { id: 'node-test', name: 'AST Validation Node', type: 'validation_gate', status: 'passed', timestamp: '10:00' },
        ],
        edges: [],
      }),
    });

    render(<Lineage />);

    await waitFor(() => {
      expect(screen.getByTestId('node-inspector-panel')).toBeDefined();
      expect(screen.getByText('AST Validation Node')).toBeDefined();
    });
  });
});
