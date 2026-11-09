import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect, vi } from 'vitest'
import { SidebarNav } from './SidebarNav'
import { NAVIGATION_CATEGORIES } from '../../navigation/navSchema'

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_key: string, options?: { defaultValue?: string }) => options?.defaultValue || _key,
  }),
}))

describe('SidebarNav (Hick\'s Law 5-choice Global Navigation)', () => {
  it('strictly renders exactly 5 top-level root categories in accordance with Hick\'s Law', () => {
    render(
      <MemoryRouter initialEntries={['/sprints']}>
        <SidebarNav />
      </MemoryRouter>
    )

    // Verify exactly 5 top-level root navigation categories exist
    expect(NAVIGATION_CATEGORIES).toHaveLength(5)
    NAVIGATION_CATEGORIES.forEach(cat => {
      expect(screen.getByTestId(`nav-root-${cat.id}`)).toBeInTheDocument()
    })
  })

  it('highlights the active category corresponding to the current URL route', () => {
    render(
      <MemoryRouter initialEntries={['/evidence']}>
        <SidebarNav />
      </MemoryRouter>
    )

    const auditBtn = screen.getByTestId('category-btn-audit')
    expect(auditBtn).toHaveClass('text-white')
  })

  it('collapses and expands sub-menus when clicking category buttons', () => {
    render(
      <MemoryRouter initialEntries={['/sprints']}>
        <SidebarNav />
      </MemoryRouter>
    )

    // Audit category has multiple items: Evidence Records & Governance Inbox
    const auditBtn = screen.getByTestId('category-btn-audit')
    expect(auditBtn).toHaveAttribute('aria-expanded', 'false')

    // Open submenu
    fireEvent.click(auditBtn)
    expect(auditBtn).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByTestId('submenu-audit')).toBeInTheDocument()
    expect(screen.getByTestId('submenu-item-evidence-records')).toBeInTheDocument()
    expect(screen.getByTestId('submenu-item-governance-review')).toBeInTheDocument()

    // Close submenu
    fireEvent.click(auditBtn)
    expect(auditBtn).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('submenu-audit')).not.toBeInTheDocument()
  })

  it('renders pending governance badge counts properly', () => {
    render(
      <MemoryRouter initialEntries={['/sprints']}>
        <SidebarNav inboxCount={4} />
      </MemoryRouter>
    )

    const auditBtn = screen.getByTestId('category-btn-audit')
    expect(auditBtn).toHaveTextContent('4')

    fireEvent.click(auditBtn)
    const reviewItem = screen.getByTestId('submenu-item-governance-review')
    expect(reviewItem).toHaveTextContent('4')
  })

  it('respects capability filtering for dynamic features', () => {
    const { rerender } = render(
      <MemoryRouter initialEntries={['/generate']}>
        <SidebarNav capabilities={{ agent_graph: false }} />
      </MemoryRouter>
    )

    // Graph topology category link should render with single link or empty
    const graphRoot = screen.getByTestId('nav-root-graph_topology')
    expect(graphRoot).toBeInTheDocument()

    // When capability enabled
    rerender(
      <MemoryRouter initialEntries={['/generate']}>
        <SidebarNav capabilities={{ agent_graph: true }} />
      </MemoryRouter>
    )
    expect(screen.getByTestId('single-category-link-graph_topology')).toBeInTheDocument()
  })

  it('toggles collapsed rail mode for responsive navigation', () => {
    const onToggle = vi.fn()
    render(
      <MemoryRouter initialEntries={['/sprints']}>
        <SidebarNav onToggleCollapse={onToggle} />
      </MemoryRouter>
    )

    const toggleBtn = screen.getByTestId('sidebar-toggle-btn')
    fireEvent.click(toggleBtn)
    expect(onToggle).toHaveBeenCalledWith(true)
  })

  it('renders rail mode with icon links when collapsed', () => {
    render(
      <MemoryRouter initialEntries={['/sprints']}>
        <SidebarNav defaultCollapsed={true} />
      </MemoryRouter>
    )

    // In collapsed rail mode, rail-category elements are rendered
    NAVIGATION_CATEGORIES.forEach(cat => {
      expect(screen.getByTestId(`rail-category-${cat.id}`)).toBeInTheDocument()
    })
  })
})
