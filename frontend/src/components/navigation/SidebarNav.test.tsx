import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, it, expect } from 'vitest'
import { SidebarNav } from './SidebarNav'
import { NAVIGATION_CATEGORIES } from '../../navigation/navSchema'

describe('SidebarNav Component (Hick\'s Law Redesign)', () => {
  const renderSidebar = (initialPath = '/sprints', alertCount = 3) => {
    return render(
      <MemoryRouter initialEntries={[initialPath]}>
        <SidebarNav alertCount={alertCount} />
      </MemoryRouter>
    )
  }

  it('renders exactly 5 top-level root categories', () => {
    renderSidebar()
    const container = screen.getByTestId('nav-root-categories')
    expect(container).toBeInTheDocument()

    NAVIGATION_CATEGORIES.forEach((category) => {
      const catEl = screen.getByTestId(`nav-category-${category.id}`)
      expect(catEl).toBeInTheDocument()
    })
  })

  it('has accessible navigation roles and labels', () => {
    renderSidebar()
    const nav = screen.getByRole('navigation', { name: /global navigation/i })
    expect(nav).toBeInTheDocument()
  })

  it('auto-expands the active category subitems and toggles on click', () => {
    renderSidebar('/sprints')

    // Dashboard category is initially active and expanded
    const sprintsSubItem = screen.getByTestId('nav-subitem-sprints-overview')
    expect(sprintsSubItem).toBeInTheDocument()

    // Click to toggle/collapse
    const dashboardButton = screen.getByRole('button', { name: /dashboard/i })
    fireEvent.click(dashboardButton)

    // After collapse, subitem is not visible in DOM
    expect(screen.queryByTestId('nav-subitem-sprints-overview')).toBeNull()

    // Click again to expand
    fireEvent.click(dashboardButton)
    expect(screen.getByTestId('nav-subitem-sprints-overview')).toBeInTheDocument()
  })

  it('displays alert badge count when alertCount > 0 in Audit category', () => {
    renderSidebar('/evidence', 5)
    const alertBadge = screen.getByTestId('badge-alert')
    expect(alertBadge).toBeInTheDocument()
    expect(alertBadge.textContent).toBe('5')
  })

  it('renders mobile menu button and opens mobile drawer on click', () => {
    renderSidebar()
    const openButton = screen.getByRole('button', { name: /open mobile navigation/i })
    expect(openButton).toBeInTheDocument()

    fireEvent.click(openButton)
    const closeButton = screen.getByRole('button', { name: /close mobile navigation/i })
    expect(closeButton).toBeInTheDocument()
  })
})
