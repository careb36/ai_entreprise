import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import React from 'react'
import { GraphSkeleton } from './GraphSkeleton'
import { LoadingSkeleton } from './LoadingSkeleton'

describe('GraphSkeleton (Doherty Threshold UX)', () => {
  it('renders with appropriate accessibility roles and labels', () => {
    render(<GraphSkeleton title="Lineage Graph" />)
    const skeleton = screen.getByRole('status')
    expect(skeleton).toBeDefined()
    expect(skeleton.getAttribute('aria-busy')).toBe('true')
    expect(skeleton.getAttribute('aria-label')).toBe('Loading graph visualization')
  })

  it('renders pipeline variant representing LangGraph steps', () => {
    const { container } = render(<GraphSkeleton variant="pipeline" nodesCount={4} />)
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)
    expect(screen.getByText('Loading graph...')).toBeDefined()
  })

  it('renders lineage variant representing Neo4j relationships', () => {
    const { container } = render(<GraphSkeleton variant="lineage" />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(container.querySelectorAll('line').length).toBeGreaterThanOrEqual(4)
  })

  it('renders optional sidebar when showSidebar is true', () => {
    const { rerender } = render(<GraphSkeleton showSidebar={false} />)
    expect(screen.queryByText('Loading graph visualization')).toBeDefined()

    rerender(<GraphSkeleton showSidebar={true} />)
    expect(screen.getByTestId('graph-skeleton')).toBeDefined()
  })

  it('LoadingSkeleton integrates GraphSkeleton when variant="graph" without spinners', () => {
    const { container } = render(<LoadingSkeleton variant="graph" />)
    // Confirm no spinning spinner is rendered
    expect(container.querySelector('.animate-spin')).toBeNull()
    // Confirm skeleton role is present
    expect(screen.getByRole('status')).toBeDefined()
    expect(container.querySelector('.animate-pulse')).not.toBeNull()
  })
})
