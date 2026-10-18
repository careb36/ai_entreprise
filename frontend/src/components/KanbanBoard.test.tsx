import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import KanbanBoard from './KanbanBoard'
import { useSprintBoard, useUpdateBacklogItem } from '../hooks/useQueries'

vi.mock('../hooks/useQueries', () => ({
  useSprintBoard: vi.fn(),
  useUpdateBacklogItem: vi.fn(),
}))

const item = {
  id: 2, sprint_id: 7, title: 'Encrypt credentials', description: '', status: 'todo', priority: 'high',
  assignee: 'Developer', comments: [], created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
}

describe('Kanban cards', () => {
  const mutate = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useSprintBoard).mockReturnValue({
      data: { sprint: { id: 7, sprint_name: 'Hardening' }, columns: { todo: [item] }, total: 1 },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as never)
    vi.mocked(useUpdateBacklogItem).mockReturnValue({ mutate, isPending: false } as never)
  })

  it('links the card title to the task page and keeps the status select', () => {
    render(
      <MemoryRouter initialEntries={['/sprints/7/board']}>
        <Routes>
          <Route path="/sprints/7/board" element={<KanbanBoard sprintId="7" />} />
          <Route path="/sprints/:sprintId/tasks/:taskId" element={<p>Task page</p>} />
        </Routes>
      </MemoryRouter>,
    )
    const link = screen.getByRole('link', { name: 'Encrypt credentials' })
    expect(link).toHaveAttribute('href', '/sprints/7/tasks/2')

    const card = link.closest('.card') as HTMLElement
    const select = within(card).getByRole('combobox', { name: 'Change status' })
    fireEvent.change(select, { target: { value: 'done' } })
    expect(mutate).toHaveBeenCalledWith({ itemId: 2, data: { status: 'done' } })

    fireEvent.click(link)
    expect(screen.getByText('Task page')).toBeInTheDocument()
  })
})
