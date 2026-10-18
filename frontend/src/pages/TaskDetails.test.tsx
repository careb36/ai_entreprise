import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TaskDetails from './TaskDetails'
import { useSprintBoard } from '../hooks/useQueries'
import { ApiError } from '../services/api'
import { useAppStore } from '../store/useAppStore'

vi.mock('../hooks/useQueries', () => ({ useSprintBoard: vi.fn() }))
const boardQuery = vi.mocked(useSprintBoard)

function setup(path = '/task/12') {
  return render(<MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/task/:taskId" element={<TaskDetails />} />
    <Route path="/sprints/:sprintId/tasks/:taskId" element={<TaskDetails />} />
  </Routes></MemoryRouter>)
}

describe('task detail errors', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useAppStore.setState({ activeSprintId: 7 })
    boardQuery.mockReturnValue({ data: { columns: {} }, isLoading: false, isError: false } as never)
  })

  it.each(['bad', '12x', '0', '-1', '1.5'])('rejects malformed task ID %s without loading a board', id => {
    setup(`/task/${id}`)
    expect(screen.getByRole('alert')).toHaveTextContent(`Invalid task ID: ${id}`)
    expect(boardQuery).toHaveBeenCalledWith(null)
  })

  it('names the task and the searched sprint when the loaded board has no task', () => {
    setup()
    expect(screen.getByRole('alert')).toHaveTextContent('Task #12 was not found in sprint #7')
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })

  it('rejects a malformed explicit sprint ID without loading a board', () => {
    setup('/sprints/7x/tasks/12')
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid sprint ID: 7x')
    expect(boardQuery).toHaveBeenCalledWith(null)
  })

  it('still names the searched sprint after the missing selection is cleared', () => {
    boardQuery.mockReturnValue({ isError: true, error: new ApiError(404, 'missing') } as never)
    const view = setup()
    useAppStore.setState({ activeSprintId: null })
    view.rerender(<MemoryRouter initialEntries={['/task/12']}><Routes>
      <Route path="/task/:taskId" element={<TaskDetails />} />
    </Routes></MemoryRouter>)
    expect(screen.getByRole('alert')).toHaveTextContent('Sprint #7 was not found while looking for task #12')
    expect(screen.getByRole('link')).toHaveAttribute('href', '/sprints')
  })

  it('distinguishes a missing sprint from a missing task', () => {
    boardQuery.mockReturnValue({ isError: true, error: new ApiError(404, 'missing') } as never)
    setup('/sprints/9/tasks/12')
    expect(screen.getByRole('alert')).toHaveTextContent('Sprint #9 was not found while looking for task #12')
    expect(boardQuery).toHaveBeenCalledWith(9)
  })

  it('names the task and sprint on load failure and retries the board', () => {
    const refetch = vi.fn()
    boardQuery.mockReturnValue({ isError: true, error: new ApiError(503, 'failed'), refetch } as never)
    setup()
    expect(screen.getByRole('alert')).toHaveTextContent('Failed to load sprint #7 for task #12')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(refetch).toHaveBeenCalledOnce()
  })

  it('offers the sprint list when there is no selected sprint', () => {
    useAppStore.setState({ activeSprintId: null })
    setup()
    expect(screen.getByRole('alert')).toHaveTextContent('Select a sprint to look up task #12')
    expect(screen.getByRole('link')).toHaveAttribute('href', '/sprints')
  })

  it.each(['/sprints/9/tasks/12', '/task/12'])('shows the Sprints › sprint › task breadcrumb at %s', path => {
    useAppStore.setState({ activeSprintId: 9 })
    boardQuery.mockReturnValue({
      data: {
        sprint: { id: 9, sprint_name: 'Hardening' },
        columns: { done: [{
          id: 12, sprint_id: 9, title: 'Encrypt credentials', description: 'Deliverable: crypto.py',
          status: 'done', priority: 'high', assignee: 'Developer', comments: [],
          created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z',
        }] },
      },
      isLoading: false,
      isError: false,
    } as never)
    setup(path)
    const breadcrumb = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(within(breadcrumb).getAllByRole('listitem').map(item => item.textContent))
      .toEqual(['Sprints', 'Hardening', 'Encrypt credentials'])
    expect(within(breadcrumb).getByRole('link', { name: 'Sprints' })).toHaveAttribute('href', '/sprints')
    expect(within(breadcrumb).getByRole('link', { name: 'Hardening' })).toHaveAttribute('href', '/sprints/9/board')
    expect(within(breadcrumb).queryByRole('link', { name: 'Encrypt credentials' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Encrypt credentials' })).toBeInTheDocument()
    expect(screen.getByText('Deliverable: crypto.py')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Back' })).not.toBeInTheDocument()
    expect(boardQuery).toHaveBeenCalledWith(9)
  })
})
