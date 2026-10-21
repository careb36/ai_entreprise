import { describe, expect, it, vi } from 'vitest'
import {
  LiveApi,
  agentsFromTasks,
  parseLiveConfig,
  pickSprint,
  readStreamEvent,
  runsFromApi,
  tasksFromBoard,
  type ApiBoard,
  type ApiRun,
} from './live'
import { withAgent } from './model'

const BOARD: ApiBoard = {
  sprint: { id: 7, sprint_name: 'Sprint 7', status: 'active' },
  columns: {
    todo: [{ id: 3, title: 'Write the docs', status: 'todo', assignee: 'TechWriter', comments: [] }],
    in_progress: [{ id: 1, title: 'Add the endpoint', status: 'in_progress', assignee: 'Developer', comments: [{ author: 'QA', text: 'NEEDS WORK: no tests', created_at: 'x' }] }],
    in_review: [{ id: 2, title: 'Fix the cache', status: 'in_review', assignee: 'QA', comments: null }],
    done: [],
    blocked: [{ id: 4, title: 'Strategy doc', status: 'blocked', assignee: 'QA', comments: [{ author: 'Quality gate', text: 'Too short', created_at: 'x' }, { author: 'system', text: 'blocked', created_at: 'y' }] }],
  },
}

describe('live mode', () => {
  it('reads its configuration from the query string, with a default person', () => {
    expect(parseLiveConfig('?api=https://api.example.test/&company=4&token=t&sprint=9&person=Ana')).toEqual({
      api: 'https://api.example.test', companyId: 4, token: 't', sprintId: 9, person: 'Ana',
    })
    expect(parseLiveConfig('?api=https://api.example.test&company=4')).toMatchObject({ person: 'Glassroom VR', token: undefined, sprintId: undefined })
    expect(parseLiveConfig('?company=4')).toBeNull()
    expect(parseLiveConfig('?api=x&company=zero')).toBeNull()
    expect(parseLiveConfig('')).toBeNull()
  })

  it('opens on the sprint asked for, else the newest running one, else the newest', () => {
    const sprints = [
      { id: 1, sprint_name: 'One', status: 'completed' },
      { id: 2, sprint_name: 'Two', status: 'active' },
      { id: 3, sprint_name: 'Three', status: 'completed' },
    ]
    expect(pickSprint(sprints, 1)?.id).toBe(1)
    expect(pickSprint(sprints)?.id).toBe(2)
    expect(pickSprint(sprints.filter(sprint => sprint.status !== 'active'))?.id).toBe(3)
    expect(pickSprint([])).toBeUndefined()
  })

  it('turns the board into tasks: rework counted from the review requests, a blocked task\'s reason from the inbox', () => {
    const tasks = tasksFromBoard(BOARD, { blocked_tasks: [{ id: 4, reason: 'Quality gate failed twice', sprint_id: 7 }] })
    expect(tasks.map(task => [task.id, task.status, task.rework])).toEqual([[1, 'in_progress', 1], [2, 'in_review', 0], [3, 'todo', 0], [4, 'blocked', 0]])
    expect(tasks[3].reason).toBe('Quality gate failed twice')
    // Without the inbox, the last comment stands in for the reason.
    expect(tasksFromBoard(BOARD)[3].reason).toBe('blocked')
  })

  it('shows one agent per role on the board, busiest state first, in the team\'s order', () => {
    const agents = agentsFromTasks(tasksFromBoard(BOARD))
    expect(agents.map(agent => [agent.role, agent.state])).toEqual([
      ['Developer', 'active'],
      ['QA', 'waiting'],
      ['TechWriter', 'idle'],
    ])
    expect(agents[0].activity).toBe('Working on "Add the endpoint"')
    expect(agents[1].activity).toBe('"Fix the cache" in review')
  })

  it('shows runs with the Verifier\'s latest checks beside the Reviewer agent\'s suggestion and the decision', () => {
    const runs: ApiRun[] = [
      { id: 10, producer: 'github', external_ref: 'pr-1', agent_role: 'Developer', status: 'refuted', latest_approval: null, suggested_decision: 'rejected', suggested_reason: 'Tests failed.', reviewed_by: 'Reviewer agent' },
      { id: 11, producer: 'copilot', external_ref: 'issue-2', status: 'ingested', latest_approval: { decision: 'approved', approver_name: 'ana' } },
    ]
    const [first, second] = runsFromApi(runs, { 10: { verdict: 'refuted', checks: [{ name: 'tests_run', outcome: 'refuted', observation: '`pytest` exited 1' }] } })
    expect(first).toMatchObject({ producer: 'github · Developer', verdict: 'refuted', observations: ['tests_run: refuted — `pytest` exited 1'], suggestion: { decision: 'rejected', reason: 'Tests failed.' } })
    expect(first.decision).toBeUndefined()
    expect(second).toMatchObject({ producer: 'copilot', verdict: 'ingested', observations: [], decision: { by: 'ana', decision: 'approved' } })
    expect(second.suggestion).toBeUndefined()
  })

  it('reads stream events as log lines and agent states, and ignores the plumbing', () => {
    expect(readStreamEvent({ ping: true })).toEqual({})
    expect(readStreamEvent({ type: 'connected', semantic_type: 'stream.connected', message: 'hi' })).toEqual({})
    const started = readStreamEvent({ type: 'agent_start', semantic_type: 'task.started', agent: 'Developer', role: 'Developer', message: 'Starting', task: 'Add the endpoint', timestamp: '2026-10-21T10:00:00Z' }, 5)
    expect(started.log).toEqual({ at: Date.parse('2026-10-21T10:00:00Z'), text: 'Developer: Starting' })
    expect(started.agent).toEqual({ role: 'Developer', state: 'active', activity: 'Starting — Add the endpoint' })
    expect(readStreamEvent({ type: 'agent_complete', semantic_type: 'task.review_handoff', role: 'QA', message: 'Handed off' }, 5).agent?.state).toBe('waiting')
    expect(readStreamEvent({ type: 'status', agent: 'System', message: 'Sprint closed' }, 5)).toEqual({ log: { at: 5, text: 'System: Sprint closed' } })
  })

  it('replaces an agent\'s card or adds one the board did not list', () => {
    const agents = [{ role: 'QA', state: 'idle' as const, activity: '' }]
    expect(withAgent(agents, { role: 'QA', state: 'active', activity: 'Reviewing' })).toEqual([{ role: 'QA', state: 'active', activity: 'Reviewing' }])
    expect(withAgent(agents, { role: 'PO', state: 'waiting', activity: 'Planning' }).map(agent => agent.role)).toEqual(['QA', 'PO'])
  })

  it('loads a company through the API and records a pinch through it', async () => {
    const calls: { url: string; init?: RequestInit }[] = []
    const answers: Record<string, unknown> = {
      '/v1/sprints?company_id=4': [{ id: 7, sprint_name: 'Sprint 7', status: 'active' }],
      '/v1/sprints/7/board': BOARD,
      '/v1/inbox?company_id=4': { blocked_tasks: [{ id: 4, reason: 'Gate', sprint_id: 7 }] },
      '/v1/agent_runs?company_id=4&exclude_in_house=true&limit=100': { agent_runs: [{ id: 10, producer: 'github', external_ref: 'pr-1', status: 'verified', latest_approval: null }] },
      '/v1/agent_runs/10/verifications?company_id=4': { verifications: [{ verdict: 'verified', checks: [{ name: 'artifact_nonempty', outcome: 'pass', observation: '12 bytes' }] }] },
      '/v1/agent_runs/10/approve': { id: 1 },
      '/v1/backlog/4': { id: 4 },
      '/v1/sprints/7/stream_ticket': { ticket: 'tkt', expires_in: 300 },
    }
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init })
      const path = url.replace('https://api.test', '')
      return { ok: path in answers, status: path in answers ? 200 : 404, json: async () => answers[path] } as Response
    })
    const api = new LiveApi({ api: 'https://api.test', companyId: 4, token: 'jwt', person: 'Ana' }, fetchImpl as unknown as typeof fetch)

    const { world, sprintId } = await api.load()
    expect(sprintId).toBe(7)
    expect(world).toMatchObject({ mode: 'live', sprint: 'Sprint 7' })
    expect(world.tasks?.find(task => task.id === 4)?.reason).toBe('Gate')
    expect(world.runs?.[0].observations).toEqual(['artifact_nonempty: pass — 12 bytes'])
    expect(calls[0].init?.headers).toEqual({ Authorization: 'Bearer jwt' })

    await api.decideRun(10, 'rejected')
    const approve = calls.find(call => call.url.endsWith('/approve'))!
    expect(JSON.parse(String(approve.init?.body))).toEqual({ company_id: 4, approver_name: 'Ana', decision: 'rejected', feedback: 'Rejected from Glassroom VR.', rejection_category: 'other' })
    await api.sendBack(4)
    const patch = calls.find(call => call.url.endsWith('/v1/backlog/4'))!
    expect([patch.init?.method, JSON.parse(String(patch.init?.body)).status]).toEqual(['PATCH', 'todo'])
    expect(await api.streamUrl(7)).toBe('https://api.test/v1/glassroom/stream?sprint_id=7&ticket=tkt')
  })
})
