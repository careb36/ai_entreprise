import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { GlobalNav } from '../GlobalNav'
import { SidebarNavigation } from '../SidebarNavigation'
import { getNavGroups, TOP_LEVEL_GROUPS_LIMIT } from '../navConfig'

// Mock react-i18next
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: { defaultValue?: string }) => opts?.defaultValue || key,
    i18n: { language: 'en', changeLanguage: vi.fn() },
  }),
}))

describe('Hick\'s Law Global Navigation', () => {
  it('enforces exactly 5 top-level navigation groups to minimize decision fatigue', () => {
    const groups = getNavGroups(0)
    expect(groups.length).toBe(5)
    expect(TOP_LEVEL_GROUPS_LIMIT).toBe(5)

    const expectedGroupIds = ['evidence', 'governance', 'executions', 'audit', 'workspace']
    expect(groups.map((g) => g.id)).toEqual(expectedGroupIds)
  })

  it('renders all 5 top-level group dropdowns in desktop mode', () => {
    render(
      <MemoryRouter initialEntries={['/evidence']}>
        <GlobalNav waitingReviewsCount={3} />
      </MemoryRouter>
    )

    const desktopNav = screen.getByTestId('desktop-nav')
    expect(desktopNav).toBeInTheDocument()

    // 5 top-level choices
    expect(screen.getByTestId('nav-group-evidence')).toBeInTheDocument()
    expect(screen.getByTestId('nav-group-governance')).toBeInTheDocument()
    expect(screen.getByTestId('nav-group-executions')).toBeInTheDocument()
    expect(screen.getByTestId('nav-group-audit')).toBeInTheDocument()
    expect(screen.getByTestId('nav-group-workspace')).toBeInTheDocument()
  })

  it('displays pending reviews badge count on the Governance group', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <GlobalNav waitingReviewsCount={5} />
      </MemoryRouter>
    )

    const badge = screen.getByTestId('nav-badge-governance')
    expect(badge).toBeInTheDocument()
    expect(badge).toHaveTextContent('5')
  })

  it('opens and closes sub-item dropdown when caret button is clicked', () => {
    render(
      <MemoryRouter initialEntries={['/evidence']}>
        <GlobalNav waitingReviewsCount={0} />
      </MemoryRouter>
    )

    // Initially sub-menu is closed
    expect(screen.queryByTestId('dropdown-menu-evidence')).not.toBeInTheDocument()

    // Click toggle button
    const toggleBtn = screen.getByRole('button', { name: /toggle evidence & graph menu/i })
    fireEvent.click(toggleBtn)

    // Now sub-menu is visible
    expect(screen.getByTestId('dropdown-menu-evidence')).toBeInTheDocument()
    expect(screen.getByText('Evidence Records')).toBeInTheDocument()
    expect(screen.getByText('Knowledge Graph')).toBeInTheDocument()

    // Click again to close
    fireEvent.click(toggleBtn)
    expect(screen.queryByTestId('dropdown-menu-evidence')).not.toBeInTheDocument()
  })

  it('closes dropdown when Escape key is pressed', () => {
    render(
      <MemoryRouter initialEntries={['/evidence']}>
        <GlobalNav waitingReviewsCount={0} />
      </MemoryRouter>
    )

    const toggleBtn = screen.getByRole('button', { name: /toggle evidence & graph menu/i })
    fireEvent.click(toggleBtn)
    expect(screen.getByTestId('dropdown-menu-evidence')).toBeInTheDocument()

    const navGroup = screen.getByTestId('nav-group-evidence')
    fireEvent.keyDown(navGroup, { key: 'Escape', code: 'Escape' })

    expect(screen.queryByTestId('dropdown-menu-evidence')).not.toBeInTheDocument()
  })

  it('renders responsive mobile sidebar with 5 grouped accordions when opened', () => {
    const handleClose = vi.fn()
    render(
      <MemoryRouter initialEntries={['/graph']}>
        <SidebarNavigation
          isOpen={true}
          onClose={handleClose}
          currentPath="/graph"
          waitingReviewsCount={2}
        />
      </MemoryRouter>
    )

    const sidebar = screen.getByTestId('responsive-sidebar')
    expect(sidebar).toBeInTheDocument()

    // Ensure 5 groups are present
    expect(screen.getByTestId('sidebar-group-evidence')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-group-governance')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-group-executions')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-group-audit')).toBeInTheDocument()
    expect(screen.getByTestId('sidebar-group-workspace')).toBeInTheDocument()

    // Click close button
    const closeBtn = screen.getByRole('button', { name: /close sidebar/i })
    fireEvent.click(closeBtn)
    expect(handleClose).toHaveBeenCalledTimes(1)
  })
})
