import { render, screen } from '@testing-library/react';
import { describe, it, expect, beforeEach } from 'vitest';
import ValidationGateDashboard from './ValidationGateDashboard';

describe('ValidationGateDashboard Doherty Threshold Performance Suite', () => {
  beforeEach(() => {
    // Reset any global mocks or performance marks
    performance.clearMarks();
    performance.clearMeasures();
  });

  it('renders core dashboard elements within the sub-400ms Doherty Threshold', async () => {
    const startTime = performance.now();

    render(
      <ValidationGateDashboard
        artifactPath="src/la_fabrique/core.py"
        initialCacheEnabled={true}
      />
    );

    const endTime = performance.now();
    const renderDurationMs = endTime - startTime;

    // Verify visual components render immediately
    expect(screen.getByTestId('validation-gate-dashboard')).toBeInTheDocument();
    
    // Assert render time strictly satisfies Doherty Threshold (<400ms)
    expect(renderDurationMs).toBeLessThan(400);
  });

  it('verifies interactive primary action buttons are responsive within target budget', async () => {
    const startTime = performance.now();

    render(
      <ValidationGateDashboard
        artifactPath="src/la_fabrique/core.py"
        initialCacheEnabled={true}
      />
    );

    const triggerBtn = screen.getByTestId('revalidate-btn');
    expect(triggerBtn).toBeEnabled();

    const elapsedTime = performance.now() - startTime;
    expect(elapsedTime).toBeLessThan(400);
  });
});
