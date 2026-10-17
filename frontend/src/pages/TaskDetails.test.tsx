import { fireEvent, render, screen } from '@testing-library/react'
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
    <Route path="/workspace/:sprintId/task/:taskId" element={<TaskDetails />} />
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
    setup('/workspace/7x/task/12')
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
    expect(screen.getByRole('link')).toHaveAttribute('href', '/dashboard')
  })

  it('distinguishes a missing sprint from a missing task', () => {
    boardQuery.mockReturnValue({ isError: true, error: new ApiError(404, 'missing') } as never)
    setup('/workspace/9/task/12')
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

  it('offers Dashboard recovery when there is no selected sprint', () => {
    useAppStore.setState({ activeSprintId: null })
    setup()
    expect(screen.getByRole('alert')).toHaveTextContent('Select a sprint to look up task #12')
    expect(screen.getByRole('link')).toHaveAttribute('href', '/dashboard')
  })
})
