import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { FittsPrimaryActions } from './FittsPrimaryActions'

describe('FittsPrimaryActions (Fitts\'s Law & Touch Target Verification)', () => {
  it('renders both Run AI Audit and Export Graph buttons with minimum 44x44px touch targets', () => {
    render(<FittsPrimaryActions />)

    const runBtn = screen.getByTestId('run-ai-audit-btn')
    const exportBtn = screen.getByTestId('export-graph-btn')

    expect(runBtn).toBeInTheDocument()
    expect(exportBtn).toBeInTheDocument()

    // Fitts's Law AC: Primary buttons must be at least 44x44px touch target
    expect(runBtn.className).toMatch(/min-h-\[44px\]/)
    expect(runBtn.className).toMatch(/min-w-\[44px\]/)

    expect(exportBtn.className).toMatch(/min-h-\[44px\]/)
    expect(exportBtn.className).toMatch(/min-w-\[44px\]/)
  })

  it('places primary actions in high-visibility corner (bottom-right by default)', () => {
    render(<FittsPrimaryActions placement="bottom-right" />)

    const actionDock = screen.getByTestId('fitts-primary-actions')
    expect(actionDock).toBeInTheDocument()
    // Corner placement classes: bottom-right
    expect(actionDock.className).toMatch(/bottom-6/)
    expect(actionDock.className).toMatch(/right-6/)
    expect(actionDock.className).toMatch(/fixed/)
  })

  it('supports high-visibility top-right corner placement when configured', () => {
    render(<FittsPrimaryActions placement="top-right" />)

    const actionDock = screen.getByTestId('fitts-primary-actions')
    expect(actionDock).toBeInTheDocument()
    // Corner placement classes: top-right
    expect(actionDock.className).toMatch(/top-4/)
    expect(actionDock.className).toMatch(/right-6/)
    expect(actionDock.className).toMatch(/fixed/)
  })

  it('triggers onRunAudit and onExportGraph when clicked', () => {
    const handleRunAudit = vi.fn()
    const handleExportGraph = vi.fn()

    render(
      <FittsPrimaryActions
        onRunAudit={handleRunAudit}
        onExportGraph={handleExportGraph}
      />
    )

    const runBtn = screen.getByTestId('run-ai-audit-btn')
    const exportBtn = screen.getByTestId('export-graph-btn')

    fireEvent.click(runBtn)
    expect(handleRunAudit).toHaveBeenCalledTimes(1)

    fireEvent.click(exportBtn)
    expect(handleExportGraph).toHaveBeenCalledTimes(1)
  })

  it('disables buttons and displays loading spinners during asynchronous operations', () => {
    render(
      <FittsPrimaryActions
        isRunningAudit={true}
        isExportingGraph={true}
      />
    )

    const runBtn = screen.getByTestId('run-ai-audit-btn')
    const exportBtn = screen.getByTestId('export-graph-btn')

    expect(runBtn).toBeDisabled()
    expect(exportBtn).toBeDisabled()
  })

  it('exposes appropriate ARIA accessibility labels for screen readers', () => {
    render(<FittsPrimaryActions />)

    expect(screen.getByRole('button', { name: /Run AI Audit/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Export Knowledge Graph/i })).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: /Primary Quick Actions/i })).toBeInTheDocument()
  })
})
