export type AgentFsmState = 'idle' | 'active' | 'waiting' | 'error'

interface AgentEventBase {
  timestamp: string
}

export interface AgentNodeStartEvent extends AgentEventBase {
  kind: 'nodeStart'
  agentRole: string
  nodeName: string
  status: string
}

export interface AgentNodeEndEvent extends AgentEventBase {
  kind: 'nodeEnd'
  agentRole: string
  nodeName: string
}

export interface AgentNodeErrorEvent extends AgentEventBase {
  kind: 'nodeError'
  agentRole: string
  nodeName: string
  error: string
}

export interface AgentMessageEvent extends AgentEventBase {
  kind: 'agentMessage'
  agentRole: string
  content: string
}

export interface AgentSystemEvent extends AgentEventBase {
  kind: 'system'
  content: string
}

export type AgentEvent =
  | AgentNodeStartEvent
  | AgentNodeEndEvent
  | AgentNodeErrorEvent
  | AgentMessageEvent
  | AgentSystemEvent

function coerceTimestamp(value: unknown): string {
  return typeof value === 'string' && value.trim().length > 0
    ? value
    : new Date().toISOString()
}

function coerceRole(value: unknown, fallback = 'System'): string {
  return typeof value === 'string' && value.trim().length > 0 ? value : fallback
}

function coerceText(value: unknown, fallback = ''): string {
  return typeof value === 'string' && value.trim().length > 0 ? value : fallback
}

function extractMessage(raw: Record<string, unknown>): string {
  return coerceText(raw.message, coerceText(raw.content, ''))
}

export function normalizeSSEEvent(raw: Record<string, unknown>): AgentEvent | null {
  const timestamp = coerceTimestamp(raw.timestamp)
  const explicitType = coerceText(raw.type)
  const role = coerceRole(raw.role, coerceRole(raw.agent))
  const task = coerceText(raw.task, extractMessage(raw))
  const message = extractMessage(raw)

  if (explicitType === 'agent_start') {
    return {
      kind: 'nodeStart',
      timestamp,
      agentRole: role,
      nodeName: task || 'task',
      status: message || `${role} started`,
    }
  }

  if (explicitType === 'agent_complete') {
    return {
      kind: 'nodeEnd',
      timestamp,
      agentRole: role,
      nodeName: task || 'task',
    }
  }

  if (explicitType === 'error') {
    return {
      kind: 'nodeError',
      timestamp,
      agentRole: role,
      nodeName: task || 'task',
      error: message || 'Unknown error',
    }
  }

  if (explicitType === 'status') {
    if (role !== 'System') {
      return {
        kind: 'nodeStart',
        timestamp,
        agentRole: role,
        nodeName: task || 'status',
        status: message || task || `${role} updated status`,
      }
    }
    return {
      kind: 'system',
      timestamp,
      content: message || task || 'System status update',
    }
  }

  if (explicitType === 'message') {
    return {
      kind: 'agentMessage',
      timestamp,
      agentRole: role,
      content: message || task,
    }
  }

  if (message && role !== 'System') {
    return {
      kind: 'agentMessage',
      timestamp,
      agentRole: role,
      content: message,
    }
  }

  if (message) {
    return {
      kind: 'system',
      timestamp,
      content: message,
    }
  }

  return null
}

const ROLE_COLOR_SEQUENCE = [
  'bg-sky-500/15 text-sky-200 border-sky-400/30',
  'bg-emerald-500/15 text-emerald-200 border-emerald-400/30',
  'bg-amber-500/15 text-amber-200 border-amber-400/30',
  'bg-rose-500/15 text-rose-200 border-rose-400/30',
  'bg-cyan-500/15 text-cyan-200 border-cyan-400/30',
  'bg-lime-500/15 text-lime-200 border-lime-400/30',
]

export function assignRolePalette(palettes: Record<string, string>, role: string): string {
  if (palettes[role]) return palettes[role]

  const normalizedRole = role.trim().toLowerCase()
  const seededPalettes: Record<string, string> = {
    po: 'bg-fuchsia-500/15 text-fuchsia-200 border-fuchsia-400/30',
    productowner: 'bg-fuchsia-500/15 text-fuchsia-200 border-fuchsia-400/30',
    scrummaster: 'bg-violet-500/15 text-violet-200 border-violet-400/30',
    dev: 'bg-sky-500/15 text-sky-200 border-sky-400/30',
    developer: 'bg-sky-500/15 text-sky-200 border-sky-400/30',
    qa: 'bg-emerald-500/15 text-emerald-200 border-emerald-400/30',
    system: 'bg-slate-500/15 text-slate-200 border-slate-400/30',
  }

  const seeded = seededPalettes[normalizedRole]
  if (seeded) return seeded

  const nextIndex = Object.keys(palettes).length % ROLE_COLOR_SEQUENCE.length
  return ROLE_COLOR_SEQUENCE[nextIndex]
}