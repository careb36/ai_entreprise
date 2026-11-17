import { fireEvent, render, screen, within } from '@testing-library/react'
import { CircleCheck, Hourglass } from 'lucide-react'
import { describe, expect, it, vi } from 'vitest'
import { CountPill, EmptyState, PageHeader, Panel, SegmentedFilter, StatusBadge, Toolbar } from '.'

// The design system's primitives (docs/contexts/frontend.md, Design system).
// Plain strings stand for what a page passes from t().
describe('the design system primitives', () => {
  it('names a count in words and shows shown/total while filtered', () => {
    const { rerender } = render(<CountPill count={3} label="3 tasks" />)
    expect(screen.getByRole('img', { name: '3 tasks' })).toHaveTextContent(/^3$/)
    rerender(<CountPill count={1} total={3} label="1 of 3 tasks shown" />)
    expect(screen.getByRole('img', { name: '1 of 3 tasks shown' })).toHaveTextContent('1/3')
    rerender(<CountPill count={3} total={3} label="3 tasks" />)
    expect(screen.getByRole('img', { name: '3 tasks' })).toHaveTextContent(/^3$/)
  })

  it('shows a status as an icon and a word', () => {
    const { container } = render(<StatusBadge icon={CircleCheck} label="Completed" tone="success" />)
    expect(screen.getByText('Completed')).toBeInTheDocument()
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })

  it('presses one preset at a time and leaves the choice to the parent', () => {
    const onChange = vi.fn()
    render(
      <SegmentedFilter label="Presets" value="all" onChange={onChange} testIdPrefix="preset" options={[
        { value: 'all', label: 'All' },
        { value: 'waiting', label: 'Waiting', icon: Hourglass, count: 2, attention: true },
      ]} />,
    )
    const group = screen.getByRole('group', { name: 'Presets' })
    expect(within(group).getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true')
    const waiting = within(group).getByRole('button', { name: /^Waiting/ })
    expect(waiting).toHaveAttribute('aria-pressed', 'false')
    expect(waiting).toHaveTextContent('2')
    expect(screen.getByTestId('preset-waiting')).toBe(waiting)
    fireEvent.click(waiting)
    expect(onChange).toHaveBeenCalledWith('waiting')
  })

  it('names a titled panel by its header, and a toolbar by its label', () => {
    render(
      <>
        <Toolbar label="Filters" sticky><button type="button">Clear</button></Toolbar>
        <Panel title="All sprints" count={<CountPill count={2} label="2 sprints" />}><p>rows</p></Panel>
      </>,
    )
    expect(screen.getByRole('group', { name: 'Filters' })).toContainElement(screen.getByRole('button', { name: 'Clear' }))
    const panel = screen.getByRole('region', { name: 'All sprints' })
    expect(within(panel).getByRole('heading', { level: 2, name: 'All sprints' })).toBeInTheDocument()
    expect(within(panel).getByRole('img', { name: '2 sprints' })).toBeInTheDocument()
  })

  it('renders an untitled panel as a plain container', () => {
    render(<Panel><p>rows</p></Panel>)
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
  })

  it("gives the page its one h1 with the subtitle and actions", () => {
    render(<PageHeader title="Sprints" subtitle="Every sprint" actions={<button type="button">Plan</button>} />)
    expect(screen.getByRole('heading', { level: 1, name: 'Sprints' })).toBeInTheDocument()
    expect(screen.getByText('Every sprint')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Plan' })).toBeInTheDocument()
  })

  it('says what is empty and offers the way to fill it', () => {
    render(<EmptyState title="No sprints yet" description="Plan one below." actions={<a href="#plan">Plan</a>} />)
    expect(screen.getByText('No sprints yet')).toBeInTheDocument()
    expect(screen.getByText('Plan one below.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Plan' })).toHaveAttribute('href', '#plan')
  })
})
