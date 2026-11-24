/**
 * The Live page's states, read from the backend's records (issue #766).
 *
 * `GET /v1/sprints/{id}/live_state` says who is working now (a run in flight,
 * only while the sprint runs), why a paused sprint is paused, when a closed
 * one closed and each health tile. The event stream only animates it: an
 * `agent_start` in the stream never makes an agent "working" on its own.
 * The definitions are in docs/contexts/frontend.md ("Statuses").
 */
import type { TFunction } from 'i18next'
import type { SprintLiveState } from '../../services/api'
import type { AgentMessage } from '../../store/agentStore'
import { parseTimestamp } from '../../lib/kanban'
import { formatAgeShort } from './format'
import { authorKey, getAgent, type AgentIdentity } from './identity'
import type { HealthPanelEntry } from './types'

export interface ActiveAgent {
  key: string
  identity: AgentIdentity
  /** Since the run started; 0 when only the task's lock shows it (another replica). */
  elapsedMs: number
  taskId: number
  taskTitle: string | null
  kind: 'task' | 'review'
}

/** The agents with a run in flight now; nobody when the sprint does not run. */
export function activeAgentsFrom(live: SprintLiveState | undefined, now: number): ActiveAgent[] {
  if (!live?.running) return []
  return live.working.map(run => {
    const identity = getAgent(run.role)
    const since = parseTimestamp(run.since)
    return {
      key: `${identity.key}-${run.task_id}-${run.kind}`,
      identity,
      elapsedMs: since === null ? 0 : Math.max(0, now - since),
      taskId: run.task_id,
      taskTitle: run.task_title,
      kind: run.kind,
    }
  })
}

/** The roles working now, by their identity key. */
export function workingKeys(active: ActiveAgent[]): Set<string> {
  return new Set(active.map(agent => agent.identity.key))
}

/** What a message group's badge says: the group's latest state, never its first event. */
export type GroupState =
  | 'working' | 'started' | 'approved' | 'refused' | 'blocked' | 'delivered' | 'error' | 'update' | 'split'

function stateOf(msg: AgentMessage): GroupState | null {
  switch (msg.semanticType) {
    case 'task.approved': return 'approved'
    case 'task.rework_scheduled': return 'refused'
    case 'task.blocked':
    case 'task.awaiting_person': return 'blocked'
  }
  const text = msg.content.trim().toUpperCase()
  if (text.startsWith('APPROVED')) return 'approved'
  if (text.startsWith('NEEDS WORK')) return 'refused'
  switch (msg.type as string) {
    case 'error': return 'error'
    case 'agent_complete': return 'delivered'
    case 'agent_start': return 'started'
    case 'status': return 'update'
    case 'needs_split': return 'split'
  }
  return null
}

/**
 * The state of a group of one author's messages: its latest message that says
 * one. A start reads "working" only while the backend says that agent has a
 * run in flight; otherwise it is a start that already happened.
 */
export function groupState(messages: AgentMessage[], working: Set<string>): GroupState | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const state = stateOf(messages[i])
    if (state === null) continue
    if (state === 'started' && working.has(getAgent(authorKey(messages[i])).key)) return 'working'
    return state
  }
  return null
}

/** Each agent of the company seen in the feed, once (the system, gates and people are not agents). */
export function agentsSeen(messages: AgentMessage[]): AgentIdentity[] {
  const seen = new Map<string, AgentIdentity>()
  for (const msg of messages) {
    const identity = getAgent(authorKey(msg))
    if (identity.isAgent) seen.set(identity.key, identity)
  }
  return [...seen.values()]
}

/** Memory retrievals seen on this stream; an event replayed after a reconnection counts once. */
export function memoryRetrievals(messages: AgentMessage[]): number {
  return messages.filter(m => m.semanticType === 'task.memory_retrieved' || m.semanticType === 'kickoff.memory_retrieved').length
}

/** Whether *msg* happened after the sprint closed (*closedAt*, naive UTC). */
export function afterClose(msg: AgentMessage, closedAt: string | null | undefined): boolean {
  const closed = parseTimestamp(closedAt)
  const at = parseTimestamp(msg.timestamp)
  return closed !== null && at !== null && at > closed
}

/** The health tiles: each from the records, labelled with exactly what it measures. */
export function healthEntries({ live, isConnected, memoryHits, now, t }: {
  live: SprintLiveState | undefined
  isConnected: boolean
  memoryHits: number
  now: number
  t: TFunction
}): HealthPanelEntry[] {
  const stream: HealthPanelEntry = {
    label: t('glassroom.healthLabels.stream'),
    value: isConnected ? t('glassroom.streamStatus.live') : t('glassroom.streamStatus.offline'),
    tone: isConnected ? 'good' : 'danger',
  }
  if (!live) return [stream]
  const tiles = live.tiles
  const ago = (ms: number) => t('glassroom.agoWithValue', { value: formatAgeShort(ms) })
  const lastEvent = parseTimestamp(tiles.last_agent_event_at)
  const daemon = tiles.daemon
  const daemonValue = daemon.seconds_since_heartbeat === null
    ? t(`glassroom.daemonState.${daemon.state}`)
    : `${t(`glassroom.daemonState.${daemon.state}`)} · ${ago(daemon.seconds_since_heartbeat * 1000)}`
  const previous = tiles.previous_sprint
  const previousRate = previous && previous.total > 0 ? Math.round((previous.done / previous.total) * 100) : null
  const running = live.running
  return [
    stream,
    {
      label: t('glassroom.healthLabels.activeAgents'),
      value: String(tiles.active_agents),
      // Nobody working in a closed or paused sprint is expected, not a warning.
      tone: tiles.active_agents > 0 ? 'good' : running ? 'warn' : 'neutral',
    },
    { label: t('glassroom.healthLabels.agentFailures'), value: String(tiles.agent_failures), tone: tiles.agent_failures > 0 ? 'danger' : 'good' },
    { label: t('glassroom.healthLabels.blockedTasks'), value: String(tiles.blocked_tasks), tone: tiles.blocked_tasks > 0 ? 'warn' : 'good' },
    { label: t('glassroom.healthLabels.openReworks'), value: String(tiles.open_reworks), tone: tiles.open_reworks > 0 ? 'warn' : 'neutral' },
    {
      label: t('glassroom.healthLabels.lastAgentEvent'),
      value: lastEvent === null ? t('glassroom.notAvailable') : ago(Math.max(0, now - lastEvent)),
      tone: 'neutral',
    },
    {
      label: t('glassroom.healthLabels.daemonHeartbeat'),
      value: daemonValue,
      tone: daemon.state === 'online' ? 'good' : daemon.state === 'stale' ? 'warn' : running ? 'danger' : 'neutral',
    },
    { label: t('glassroom.healthLabels.memoryHits'), value: String(memoryHits), tone: memoryHits > 0 ? 'good' : 'neutral' },
    ...(previous && previousRate !== null ? [{
      label: t('glassroom.healthLabels.previousSprint', { name: previous.name }),
      value: t('glassroom.previousSprintValue', { done: previous.done, total: previous.total, rate: previousRate }),
      tone: (previousRate >= 80 ? 'good' : previousRate >= 50 ? 'warn' : 'danger') as HealthPanelEntry['tone'],
    }] : []),
  ]
}
