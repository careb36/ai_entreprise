import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { TaskListView } from './TaskListView'
import type { FilteredTask } from '../services/api'

function task(id: number, patch: Partial<FilteredTask> = {}): FilteredTask {
  return {
    id, sprint_id: 8, title: `Task ${id}`, status: 'todo', priority: 'medium', assignee: 'Developer',
    assignee_role: 'Developer', kind: null, covers: [], updated_at: null, verdict: null, risk_tier: null,
    producer: null, rework_count: 0, waits_for_person: false, ...patch,
  }
}

const TASKS = [
  task(1, { status: 'blocked', verdict: 'refuted', risk_tier: 'high', producer: 'le-fabrique-task-worker', waits_for_person: true }),
  task(2, { status: 'done', verdict: 'verified', risk_tier: 'low', covers: ['REQ-3.1'] }),
  task(3, { status: 'blocked' }),
]

function renderList(props: Partial<Parameters<typeof TaskListView>[0]> = {}) {
  return render(
    <MemoryRouter>
      <TaskListView tasks={TASKS} columns={['title', 'status', 'verdict', 'risk_tier', 'waits_for_person']} layout="list" sprintId={8} {...props} />
    </MemoryRouter>,
  )
}

describe('TaskListView', () => {
  it('lists the tasks with the view\'s columns, each linking to the task', () => {
    renderList()
    const table = screen.getByRole('table', { name: 'Tasks that match the filters' })
    expect(within(table).getAllByRole('columnheader').map(h => h.textContent)).toEqual(
      ['Title', 'Status', 'Verdict', 'Risk tier', 'Waits for a person'],
    )
    const first = within(table).getAllByRole('row')[1]
    expect(first).toHaveTextContent('Task 1BlockedRefutedHighYes')
    expect(within(first).getByRole('link', { name: 'Task 1' })).toHaveAttribute('href', '/sprints/8/tasks/1')
    expect(within(table).getAllByRole('row')[3]).toHaveTextContent('Task 3Blocked——No')
  })

  it('groups the list when the view says so', () => {
    renderList({ groupBy: 'status' })
    const blocked = screen.getByRole('region', { name: 'Blocked' })
    expect(within(blocked).getAllByRole('row')).toHaveLength(3)
    expect(within(screen.getByRole('region', { name: 'Done' })).getByText('Task 2')).toBeInTheDocument()
  })

  it('shows the same tasks as read-only columns by status on the board layout', () => {
    renderList({ layout: 'board' })
    const board = screen.getByTestId('filtered-board')
    expect(within(board).getByRole('region', { name: 'Blocked' })).toHaveTextContent('2')
    expect(within(board).getByRole('region', { name: 'Done' })).toHaveTextContent('Task 2Verified · Low')
    expect(within(board).getByRole('region', { name: 'To do' })).toHaveTextContent('0')
  })

  it('says so when nothing matches', () => {
    renderList({ tasks: [] })
    expect(screen.getByRole('status')).toHaveTextContent('No task matches these filters.')
  })
})
