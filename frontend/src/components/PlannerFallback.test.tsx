import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ObjectiveText, PlannerFallbackNote } from './PlannerFallback'

describe('the planner fallback note', () => {
  it('shows nothing when the model answered', () => {
    const { container } = render(<PlannerFallbackNote fallback={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('names the observed reason and offers a retry only when given one', () => {
    const onRetry = vi.fn()
    const { unmount } = render(<PlannerFallbackNote fallback={{ reason: 'unparseable', detail: 'JSONDecodeError: x' }} onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toHaveTextContent("The planning model's answer could not be read as a plan.")
    fireEvent.click(screen.getByRole('button', { name: 'Retry planning' }))
    expect(onRetry).toHaveBeenCalledOnce()
    unmount()

    render(<PlannerFallbackNote fallback={{ reason: 'something new', detail: '' }} />)
    expect(screen.getByRole('alert')).toHaveTextContent('Planning failed with an unexpected error.')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('shows the objective whole on demand', () => {
    const objective = 'x'.repeat(5000)
    render(<ObjectiveText objective={objective} />)
    expect(screen.getByText('The objective as written (5000 characters)')).toBeInTheDocument()
    expect(screen.getByText(objective)).toBeInTheDocument()
  })
})
