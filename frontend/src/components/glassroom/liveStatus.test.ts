import { describe, expect, it } from 'vitest'
import type { TFunction } from 'i18next'
import i18n from '../../i18n'
import type { SprintLiveState } from '../../services/api'
import type { AgentMessage } from '../../store/agentStore'
import { insertByTime } from '../../store/agentStore'
import { getAgent } from './identity'
import { activeAgentsFrom, afterClose, agentsSeen, groupState, healthEntries, memoryRetrievals, workingKeys } from './liveStatus'

const t = i18n.getFixedT('en') as TFunction

function msg(id: string, fields: Partial<AgentMessage>): AgentMessage {
  return { id, timestamp: '2026-11-24T14:46:00Z', agent: 'QA', role: 'QA', content: '', type: 'message', ...fields }
}

function live(fields: Partial<SprintLiveState> = {}): SprintLiveState {
  return {
    sprint_id: 8, status: 'completed', running: false, pause_reason: null, pause_kind: null, closed_at: '2026-11-24T14:46:40',
    working: [{ role: 'Dev', task_id: 37, task_title: 'Changelog', kind: 'task', since: '2026-11-24T14:40:00Z' }],
    tiles: {
      active_agents: 0, agent_failures: 1, blocked_tasks: 0, open_reworks: 0, last_agent_event_at: '2026-11-24T14:46:40',
      daemon: { state: 'not_observed', seconds_since_heartbeat: null }, previous_sprint: null,
    },
    ...fields,
  }
}

describe('who is working', () => {
  it('nobody in a completed, paused or cancelled sprint, whatever runs the backend lists', () => {
    for (const status of ['completed', 'paused', 'cancelled']) {
      expect(activeAgentsFrom(live({ status, running: false }), Date.now())).toEqual([])
    }
  })

  it('the runs in flight while the sprint runs, with their role and task', () => {
    const now = Date.parse('2026-11-24T14:41:00Z')
    const [agent] = activeAgentsFrom(live({ status: 'active', running: true }), now)
    expect(agent.identity.key).toBe('Developer')
    expect(agent.taskTitle).toBe('Changelog')
    expect(agent.elapsedMs).toBe(60_000)
  })
})

describe("a group's badge", () => {
  const review = [
    msg('1', { type: 'agent_start', semanticType: 'task.review_started', content: 'QA reviews' }),
    msg('2', { type: 'agent_complete', semanticType: 'task.approved', content: 'QA approved → done' }),
  ]

  it("is the group's latest state, not its first event", () => {
    expect(groupState(review, new Set(['QA']))).toBe('approved')
    expect(groupState([msg('3', { type: 'status', semanticType: 'task.rework_scheduled' })], new Set())).toBe('refused')
    expect(groupState([msg('4', { type: 'error', semanticType: 'task.blocked' })], new Set())).toBe('blocked')
    expect(groupState([msg('5', { content: 'NEEDS WORK: tests failed', agent: 'Verifier', role: 'Verifier' })], new Set())).toBe('refused')
    expect(groupState([msg('6', { type: 'agent_complete' })], new Set())).toBe('delivered')
  })

  it('reads working only while the backend says that agent has a run in flight', () => {
    expect(groupState(review.slice(0, 1), new Set(['QA']))).toBe('working')
    expect(groupState(review.slice(0, 1), new Set())).toBe('started')
  })
})

describe('agent identities', () => {
  it('resolve to the company roles, never a phantom generic Agent', () => {
    const seen = agentsSeen([
      msg('a', { agent: 'Dev', role: 'Dev' }),
      msg('b', { agent: 'Developer', role: 'Developer' }),
      msg('c', { agent: 'Verifier', role: 'Verifier' }),
      msg('d', { agent: 'user', role: 'user' }),
      msg('e', { agent: 'System', role: 'System' }),
      msg('f', { agent: 'Security', role: 'Security' }),
      msg('g', { agent: 'Quality gate', role: 'Quality gate' }),
    ])
    expect(seen.map(i => i.label)).toEqual(['Developer', 'SecOps'])
    for (const name of ['Verifier', 'Quality gate', 'user', 'Architect', 'DevOps', 'Data']) {
      expect(getAgent(name).label).not.toBe('Agent')
    }
    expect(getAgent('Ana').label).toBe('Ana')
  })
})

describe('the health tiles', () => {
  it('are labelled with what they measure and read from the records', () => {
    const entries = healthEntries({ live: live(), isConnected: true, memoryHits: 0, now: Date.parse('2026-11-24T14:54:49Z'), t })
    const byLabel = Object.fromEntries(entries.map(e => [e.label, e]))
    expect(byLabel['Agents with a run in flight']).toMatchObject({ value: '0', tone: 'neutral' })
    expect(byLabel['Failed agent calls (review or model)'].value).toBe('1')
    expect(byLabel['Tasks with an unanswered rework request'].value).toBe('0')
    expect(byLabel['Last event an agent recorded'].value).toBe('8m 9s ago')
    expect(byLabel['Workspace daemon heartbeat'].value).toBe('no heartbeat seen')
    expect(byLabel['Memory retrievals seen on this stream'].value).toBe('0')
    expect(entries.map(e => e.label)).not.toContain('Freshest heartbeat')
  })

  it('names the previous sprint and its tally', () => {
    const state = live({ tiles: { ...live().tiles, previous_sprint: { id: 7, name: 'Sprint 7', done: 0, total: 4 } } })
    const entries = healthEntries({ live: state, isConnected: true, memoryHits: 0, now: Date.now(), t })
    expect(entries.find(e => e.label === 'Previous sprint (Sprint 7)')).toMatchObject({ value: '0/4 done (0%)', tone: 'danger' })
  })

  it('counts a memory retrieval replayed after a reconnection once', () => {
    expect(memoryRetrievals([msg('m', { semanticType: 'task.memory_retrieved' })])).toBe(1)
  })
})

describe('the order and the close', () => {
  it('puts each event at its own time, whatever order it arrived in', () => {
    let feed: AgentMessage[] = []
    feed = insertByTime(feed, msg('late', { timestamp: '2026-11-24T14:46:40+00:00' }))
    feed = insertByTime(feed, msg('early', { timestamp: '2026-11-24T13:50:36+00:00' }))
    feed = insertByTime(feed, msg('mid', { timestamp: '2026-11-24 14:00:00' }))
    expect(feed.map(m => m.id)).toEqual(['early', 'mid', 'late'])
  })

  it('marks what happened after the sprint closed', () => {
    expect(afterClose(msg('w', { timestamp: '2026-11-24T14:52:00Z' }), '2026-11-24T14:46:40')).toBe(true)
    expect(afterClose(msg('q', { timestamp: '2026-11-24T14:46:40Z' }), '2026-11-24T14:46:40')).toBe(false)
    expect(afterClose(msg('x', {}), null)).toBe(false)
  })

  it('reads the working set by identity key', () => {
    expect([...workingKeys(activeAgentsFrom(live({ running: true }), Date.now()))]).toEqual(['Developer'])
  })
})
