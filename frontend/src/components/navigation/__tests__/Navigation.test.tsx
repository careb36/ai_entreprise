import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SidebarNavigation } from '../SidebarNavigation'
import { GlobalNav } from '../GlobalNav'
import {
  NAV_ITEMS,
  HICKS_LAW_NAV_LIMIT,
  filterNavByCapabilities,
  isNavGroupActive,
} from '../navConfig'

describe("Hick's Law Navigation Architecture", () => {
  it('enforces Hick’s Law with at most 5 top-level choices', () => {
    expect(HICKS_LAW_NAV_LIMIT).toBe(5)
    expect(NAV_ITEMS.length).toBeLessThanOrEqual(HICKS_LAW_NAV_LIMIT)
    expect(NAV_ITEMS.length).toBe(5)

    const ids = NAV_ITEMS.map(item => item.id)
    expect(ids).toEqual(['sprints', 'evidence', 'governance', 'generate', 'memory'])
  })

  it('filters capabilities while maintaining <= 5 top-level choices', () => {
    const all = filterNavByCapabilities(NAV_ITEMS, { agent_graph: true, memory: true })
    expect(all.length).toBe(5)

    const withoutOptional = filterNavByCapabilities(NAV_ITEMS, { agent_graph: false, memory: false })
    expect(withoutOptional.length).toBe(3)
    expect(withoutOptional.map(i => i.id)).toEqual(['sprints', 'evidence', 'governance'])
  })

  it('accurately identifies active groups including nested child routes', () => {
    const sprintsGroup = NAV_ITEMS.find(item => item.id === 'sprints')!
    expect(isNavGroupActive(sprintsGroup, '/sprints')).toBe(true)
    expect(isNavGroupActive(sprintsGroup, '/sprints/123')).toBe(true)
    expect(isNavGroupActive(sprintsGroup, '/sprints?view=tasks')).toBe(true)
    expect(isNavGroupActive(sprintsGroup, '/governance')).toBe(false)
  })
})

describe('SidebarNavigation Component', () => {
  const renderSidebar = (props = {}) => {
    return render(
      <MemoryRouter initialEntries={['/sprints']}>
        <SidebarNavigation {...props} />
      </MemoryRouter>
    )
  }

  it('renders all 5 top-level items on desktop', () => {
    renderSidebar()

    expect(screen.getByText('Sprints & Work')).toBeDefined()
    expect(screen.getByText('Evidence & Runs')).toBeDefined()
    expect(screen.getByText('Governance & Review')).toBeDefined()
    expect(screen.getByText('Agent Studio')).toBeDefined()
    expect(screen.getByText('Knowledge & Memory')).toBeDefined()
  })

  it('expands and collapses group sub-items when toggled', () => {
    renderSidebar()

    // Sprints sub-items should be present when expanded
    const toggleButton = screen.getByLabelText('Toggle Sprints & Work sub-items')
    expect(toggleButton).toBeDefined()

    // Toggle collapse
    fireEvent.click(toggleButton)
    expect(toggleButton.getAttribute('aria-expanded')).toBe('false')

    // Toggle expand
    fireEvent.click(toggleButton)
    expect(toggleButton.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText('All Sprints')).toBeDefined()
    expect(screen.getByText('Task Execution')).toBeDefined()
  })

  it('displays badge counters accurately', () => {
    renderSidebar({
      badges: { waiting: 7, activeTasks: 3 },
    })

    expect(screen.getByText('7')).toBeDefined()
    expect(screen.getByText('3')).toBeDefined()
  })

  it('handles desktop collapse toggle state', () => {
    renderSidebar()

    const collapseButton = screen.getByLabelText('Collapse sidebar')
    fireEvent.click(collapseButton)

    expect(screen.getByLabelText('Expand sidebar')).toBeDefined()
  })

  it('supports mobile off-canvas drawer open and close triggers', () => {
    const handleClose = vi.fn()
    renderSidebar({
      isOpenMobile: true,
      onCloseMobile: handleClose,
    })

    const backdrop = screen.getByTestId('mobile-backdrop')
    fireEvent.click(backdrop)
    expect(handleClose).toHaveBeenCalled()

    const closeBtn = screen.getByLabelText('Close navigation')
    fireEvent.click(closeBtn)
    expect(handleClose).toHaveBeenCalledTimes(2)
  })
})

describe('GlobalNav Integration', () => {
  it('renders the top header with mobile toggle and content', () => {
    render(
      <MemoryRouter initialEntries={['/governance']}>
        <GlobalNav appName="La Fabrique" waitingCount={4}>
          <div data-testid="page-content">Dashboard Content</div>
        </GlobalNav>
      </MemoryRouter>
    )

    expect(screen.getByText('La Fabrique')).toBeDefined()
    expect(screen.getByText('4 Reviews')).toBeDefined()
    expect(screen.getByTestId('page-content')).toBeDefined()

    // Toggle mobile drawer
    const mobileToggle = screen.getByTestId('mobile-menu-toggle')
    fireEvent.click(mobileToggle)

    expect(screen.getByTestId('mobile-drawer')).toBeDefined()
  })
})
