import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import GlassRoom from './GlassRoom'

const feed = vi.hoisted(() => ({ messages: [] as unknown[], board: undefined as unknown, live: undefined as unknown }))

vi.mock('../hooks/useQueries', () => ({
  useSprintBoard: () => ({ data: feed.board }),
  useSprintLiveState: () => ({ data: feed.live }),
  useQuotas: () => ({ data: [] }),
}))

vi.mock('../store/agentStore', () => ({
  useAgentStore: () => ({
    messages: feed.messages,
    agentStates: {
      Developer: {
        role: 'Developer',
        fsmState: 'waiting',
        currentActivity: 'Browser agent activity',
        lastSeen: new Date().toISOString(),
      },
    },
    isConnected: true,
    connectionError: null,
    connect: vi.fn(),
    disconnect: vi.fn(),
    lastRetro: null,
  }),
}))

afterEach(() => {
  vi.unstubAllGlobals()
  feed.messages = []
  feed.board = undefined
  feed.live = undefined
})

describe('GlassRoom scroll clearance', () => {
  it('reserves chat-button clearance in each desktop scroll pane without shrinking the roster', () => {
    vi.stubGlobal('requestAnimationFrame', vi.fn())
    const { container } = render(<GlassRoom sprintId="42" />)

    const scrollPanes = container.querySelectorAll('[class~="xl:pb-24"]')
    expect(scrollPanes).toHaveLength(3)
    for (const pane of scrollPanes) {
      expect(pane.className).toMatch(/(?:lg|xl):overflow-y-auto/)
    }

    const roster = screen.getByText(/Browser agent activity/).closest('.space-y-px')
    expect(roster).toHaveClass('shrink-0')
    expect(roster).not.toHaveClass('lg:min-h-0')
  })
})

describe('GlassRoom task links', () => {
  it('links task events that carry a task_id, and sprint tasks, to the task page', () => {
    vi.stubGlobal('requestAnimationFrame', vi.fn())
    const timestamp = new Date().toISOString()
    feed.messages = [
      { id: 'a', timestamp, agent: 'Developer', task: 'Encrypt credentials', taskId: 2, content: 'Working on it', type: 'agent_start' },
      { id: 'b', timestamp, agent: 'QA', task: 'Untracked chore', content: 'No backlog id', type: 'message' },
    ]
    feed.board = {
      sprint: { id: 42, sprint_name: 'Hardening', status: 'active', scope_size: 'M' },
      columns: { todo: [{ id: 5, sprint_id: 42, title: 'Write runbook', status: 'todo', priority: 'low', comments: [] }] },
      total: 1,
    }
    render(<MemoryRouter><GlassRoom sprintId="42" /></MemoryRouter>)

    const eventLink = screen.getByRole('link', { name: 'Encrypt credentials' })
    expect(eventLink).toHaveAttribute('href', '/sprints/42/tasks/2')
    expect(screen.queryByRole('link', { name: 'Untracked chore' })).not.toBeInTheDocument()
    expect(screen.getAllByText('Untracked chore').length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: 'Write runbook' })).toHaveAttribute('href', '/sprints/42/tasks/5')
  })
})

/** Sprint 8 as the owner saw it: completed at 11:46 with every task done. */
const CLOSED_LIVE = {
  sprint_id: 8, status: 'completed', running: false, pause_reason: null, pause_kind: null,
  closed_at: '2026-11-24T14:46:40',
  // A stale run must not show either: a closed sprint has nobody working.
  working: [{ role: 'QA', task_id: 37, task_title: 'Changelog', kind: 'review', since: '2026-11-24T14:46:00' }],
  tiles: {
    active_agents: 0, agent_failures: 0, blocked_tasks: 0, open_reworks: 0,
    last_agent_event_at: '2026-11-24T14:46:40', daemon: { state: 'online', seconds_since_heartbeat: 4 },
    previous_sprint: { id: 7, name: 'Sprint 7', done: 0, total: 4 },
  },
}

const SPRINT_8_FEED = [
  { id: 'v1', timestamp: '2026-11-24T14:45:03Z', agent: 'Verifier', role: 'Verifier', taskId: 37, task: 'Changelog', content: 'NEEDS WORK: tests failed', type: 'message' },
  { id: 'd1', timestamp: '2026-11-24T14:46:04Z', agent: 'Dev', role: 'Dev', taskId: 37, task: 'Changelog', content: 'Delivered', type: 'agent_start' },
  { id: 'q1', timestamp: '2026-11-24T14:46:05Z', agent: 'QA', role: 'QA', taskId: 37, task: 'Changelog', content: 'QA reviews the delivery', type: 'agent_start', semanticType: 'task.review_started' },
  { id: 'q2', timestamp: '2026-11-24T14:46:40Z', agent: 'QA', role: 'QA', taskId: 37, task: 'Changelog', content: 'QA approved → done', type: 'agent_complete', semanticType: 'task.approved' },
  { id: 'w1', timestamp: '2026-11-24T14:52:00Z', agent: 'System', content: 'Workspace command finished successfully: npm ci', type: 'status' },
]

describe('GlassRoom states from the records', () => {
  it('shows nobody working in a completed sprint and each group by its latest state', () => {
    vi.stubGlobal('requestAnimationFrame', vi.fn())
    feed.live = CLOSED_LIVE
    feed.messages = SPRINT_8_FEED
    feed.board = { sprint: { id: 8, sprint_name: 'Sprint 8', status: 'completed', scope_size: 'M' }, columns: {}, total: 0 }
    const { container } = render(<MemoryRouter><GlassRoom sprintId="8" /></MemoryRouter>)

    // The QA group began with a start and ended approved: the badge says approved.
    const badges = [...container.querySelectorAll('[data-state]')].map(b => b.getAttribute('data-state'))
    expect(badges).toContain('approved')
    expect(badges).not.toContain('working')
    expect(screen.queryByText('working')).not.toBeInTheDocument()
    expect(screen.getAllByText('not working').length).toBeGreaterThan(0)
    // The tiles say what they measure, from the records.
    expect(screen.getByText('Agents with a run in flight')).toBeInTheDocument()
    expect(screen.getByText('Tasks with an unanswered rework request')).toBeInTheDocument()
    expect(screen.getByText('Last event an agent recorded')).toBeInTheDocument()
    expect(screen.getByText('Workspace daemon heartbeat')).toBeInTheDocument()
    expect(screen.queryByText(/heartbeat más fresco|Freshest heartbeat/i)).not.toBeInTheDocument()
    // The daemon's command came after the close, and says so.
    expect(screen.getByRole('separator')).toHaveTextContent('After the sprint closed')
  })

  it('lists the real roles, never a generic Agent, and the Verifier is not an agent', () => {
    vi.stubGlobal('requestAnimationFrame', vi.fn())
    feed.live = CLOSED_LIVE
    feed.messages = SPRINT_8_FEED
    render(<MemoryRouter><GlassRoom sprintId="8" /></MemoryRouter>)
    const roster = screen.getByText('Agents').parentElement as HTMLElement
    expect(roster).toHaveTextContent('QA Engineer')
    expect(roster).toHaveTextContent('Developer')
    expect(roster).not.toHaveTextContent('Verifier')
    expect(screen.queryByText('Agent')).not.toBeInTheDocument()
    // The Verifier's message is shown under its own name.
    expect(screen.getAllByText('Verifier').length).toBeGreaterThan(0)
  })

  it('shows an agent working only while the backend has its run in flight', () => {
    vi.stubGlobal('requestAnimationFrame', vi.fn())
    feed.live = { ...CLOSED_LIVE, status: 'active', running: true, closed_at: null, tiles: { ...CLOSED_LIVE.tiles, active_agents: 1 } }
    feed.messages = SPRINT_8_FEED.filter(m => m.id === 'q1')
    const { container } = render(<MemoryRouter><GlassRoom sprintId="8" /></MemoryRouter>)
    expect([...container.querySelectorAll('[data-state]')].map(b => b.getAttribute('data-state'))).toContain('working')
    expect(screen.getByText(/reviewing the delivery/)).toBeInTheDocument()
  })
})
