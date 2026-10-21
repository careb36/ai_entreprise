/**
 * Live mode: the room reads a real company through the same REST API the
 * web app uses, and records a person's pinches through the same endpoints.
 *
 * `?api=<backend url>&company=<id>` turns it on; `&token=<jwt>` when the
 * backend requires a login, `&sprint=<id>` to pick a sprint (else the newest
 * one that is running), `&person=<name>` for who the decisions are recorded
 * as. The adapters below are pure, so they are tested without a network.
 */

import type { Agent, AgentState, Decision, LogEntry, Run, Task, TaskStatus, Verdict, World } from './model'

export interface LiveConfig {
  api: string
  companyId: number
  token?: string
  sprintId?: number
  person: string
}

export const DEFAULT_PERSON = 'Glassroom VR'
/** Runs whose verifications are read in one load: the inbox shows the first ones anyway. */
export const RUNS_WITH_OBSERVATIONS = 12
/** The latest messages between roles shown in the log when the room opens. */
export const MESSAGES_IN_LOG = 12

export function parseLiveConfig(search: string): LiveConfig | null {
  const params = new URLSearchParams(search)
  const api = (params.get('api') ?? '').replace(/\/+$/, '')
  const companyId = Number(params.get('company'))
  if (!api || !Number.isInteger(companyId) || companyId <= 0) return null
  const sprint = Number(params.get('sprint'))
  return {
    api,
    companyId,
    token: params.get('token') || undefined,
    sprintId: Number.isInteger(sprint) && sprint > 0 ? sprint : undefined,
    person: params.get('person')?.trim() || DEFAULT_PERSON,
  }
}

// ── The API's shapes, as far as the room reads them ─────────────────────────

export interface ApiSprint { id: number; sprint_name: string; status: string; start_date?: string | null }
export interface ApiBacklogItem {
  id: number
  title: string
  status: TaskStatus
  assignee?: string | null
  comments?: { author: string; text: string; created_at: string }[] | null
}
export interface ApiBoard { sprint: { id: number; sprint_name: string; status: string }; columns: Record<string, ApiBacklogItem[]> }
export interface ApiInbox { blocked_tasks: { id: number; reason: string; sprint_id: number; frozen?: boolean }[] }

const STATUSES: TaskStatus[] = ['todo', 'in_progress', 'in_review', 'done', 'blocked']
export interface ApiRun {
  id: number
  producer: string
  external_ref: string
  agent_role?: string | null
  status: Verdict
  latest_approval: { decision: Decision; approver_name: string } | null
  suggested_decision?: Decision | null
  suggested_reason?: string | null
  reviewed_by?: string | null
}
export interface ApiVerification { verdict: Verdict; checks: { name: string; outcome: string; observation: string }[] | null }
export interface ApiMessage {
  id: number
  from_role: string
  /** null: the whole team. */
  to_role: string | null
  kind: string
  body: string
  created_at: string | null
}

/** The conversation between roles as log lines: who spoke to whom, what kind of message, the first words. */
export function logFromMessages(messages: ApiMessage[], now = Date.now()): LogEntry[] {
  return messages.map((message, index) => ({
    at: (message.created_at && Date.parse(message.created_at)) || now - (messages.length - index),
    text: `${message.from_role} › ${message.to_role ?? 'the team'} (${message.kind}): ${message.body.replace(/\s+/g, ' ').trim().slice(0, 120)}`,
  }))
}

export const REWORK_PREFIX = 'NEEDS WORK:'
export const SEND_BACK_COMMENT = 'Sent back to work from Glassroom VR.'

/** The sprint the room opens on: the one asked for when it exists, else the newest active one, else the newest. */
export function pickSprint(sprints: ApiSprint[], wanted?: number): ApiSprint | undefined {
  const asked = wanted ? sprints.find(sprint => sprint.id === wanted) : undefined
  if (asked) return asked
  const byNewest = [...sprints].sort((a, b) => b.id - a.id)
  return byNewest.find(sprint => sprint.status === 'active') ?? byNewest[0]
}

/**
 * The board's items as the room's tasks. A blocked task waits for a person
 * only when the inbox lists it (the worker still retries the others), with
 * the inbox's reason; a status the room does not know lands in "to do", as
 * the backend's own board does.
 */
export function tasksFromBoard(board: ApiBoard, inbox?: ApiInbox): Task[] {
  const waiting = new Map((inbox?.blocked_tasks ?? []).map(task => [task.id, task]))
  const tasks: Task[] = []
  for (const items of Object.values(board.columns)) {
    for (const item of items) {
      const comments = item.comments ?? []
      const status = STATUSES.includes(item.status) ? item.status : 'todo'
      const listed = waiting.get(item.id)
      tasks.push({
        id: item.id,
        title: item.title,
        status,
        assignee: item.assignee ?? undefined,
        rework: comments.filter(comment => (comment.text ?? '').trimStart().startsWith(REWORK_PREFIX)).length,
        ...(status === 'blocked'
          ? {
              reason: listed?.reason ?? comments.at(-1)?.text?.slice(0, 200),
              forPerson: inbox ? listed !== undefined : undefined,
              frozen: listed?.frozen ?? false,
            }
          : {}),
      })
    }
  }
  return tasks.sort((a, b) => a.id - b.id)
}

const AGENT_ORDER = ['PO', 'Architect', 'ScrumMaster', 'Developer', 'QA', 'SecOps', 'TechWriter']

/** One card per role seen on the board: active with a task in progress, waiting with one in review. */
export function agentsFromTasks(tasks: Task[]): Agent[] {
  const roles = new Map<string, Agent>()
  const rank = (status: TaskStatus) => (status === 'in_progress' ? 2 : status === 'in_review' ? 1 : 0)
  for (const task of tasks) {
    if (!task.assignee) continue
    const current = roles.get(task.assignee)
    const state: AgentState = task.status === 'in_progress' ? 'active' : task.status === 'in_review' ? 'waiting' : 'idle'
    const activity = task.status === 'in_progress' ? `Working on "${task.title}"` : task.status === 'in_review' ? `"${task.title}" in review` : task.status === 'blocked' ? `"${task.title}" blocked` : `Last: "${task.title}"`
    if (!current || rank(task.status) > (current.state === 'active' ? 2 : current.state === 'waiting' ? 1 : 0)) {
      roles.set(task.assignee, { role: task.assignee, state, activity })
    }
  }
  return [...roles.values()].sort((a, b) => {
    const ia = AGENT_ORDER.indexOf(a.role)
    const ib = AGENT_ORDER.indexOf(b.role)
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.role.localeCompare(b.role)
  })
}

/** Other producers' runs with the Verifier's latest checks as observations and the Reviewer agent's suggestion beside them. */
export function runsFromApi(runs: ApiRun[], verifications: Record<number, ApiVerification | undefined>): Run[] {
  return runs.map(run => {
    const latest = verifications[run.id]
    const checks = latest?.checks ?? []
    return {
      id: run.id,
      producer: run.agent_role ? `${run.producer} · ${run.agent_role}` : run.producer,
      externalRef: run.external_ref,
      verdict: latest?.verdict ?? run.status,
      observations: checks.map(check => `${check.name}: ${check.outcome} — ${check.observation}`),
      suggestion: run.suggested_decision ? { decision: run.suggested_decision, reason: run.suggested_reason ?? '' } : undefined,
      decision: run.latest_approval ? { by: run.latest_approval.approver_name, decision: run.latest_approval.decision } : undefined,
    }
  })
}

// ── Glassroom stream events as log lines and agent states ───────────────────

export interface StreamEvent {
  type?: string
  semantic_type?: string
  source?: string
  event_id?: string
  agent?: string
  role?: string
  message?: string
  task?: string
  timestamp?: string
  ping?: boolean
}

const ACTIVE = new Set(['task.started', 'task.claimed', 'task.progress', 'task.rework_scheduled'])
const WAITING = new Set(['task.review_handoff', 'task.review_started', 'task.agent_complete', 'task.approved'])
const QUIET = new Set(['stream.connected', 'stream.unavailable'])

/**
 * What a stream event means for the room: a log line, and an agent's new
 * state. Nothing for pings, plumbing, and the history the backend replays
 * on every connection (the room's log opened with the conversation, and a
 * replayed comment says nothing about what an agent does now).
 */
export function readStreamEvent(event: StreamEvent, at = Date.now()): { log?: LogEntry; agent?: Agent } {
  if (event.ping || !event.message || event.source === 'history' || QUIET.has(event.semantic_type ?? '')) return {}
  const who = event.agent || event.role || 'System'
  const log: LogEntry = { at: event.timestamp ? Date.parse(event.timestamp) || at : at, text: `${who}: ${event.message}` }
  const role = event.role || event.agent
  if (!role || role === 'System') return { log }
  const kind = event.semantic_type ?? ''
  const state: AgentState | undefined = ACTIVE.has(kind) || event.type === 'agent_start' ? 'active' : WAITING.has(kind) || event.type === 'agent_complete' ? 'waiting' : event.type === 'error' || kind === 'task.blocked' ? 'error' : undefined
  if (!state) return { log }
  return { log, agent: { role, state, activity: event.task ? `${event.message.slice(0, 60)} — ${event.task}` : event.message.slice(0, 80) } }
}

// ── The network ─────────────────────────────────────────────────────────────

type Fetch = typeof fetch

export class LiveApi {
  constructor(private readonly config: LiveConfig, private readonly fetchImpl: Fetch = (...args) => fetch(...args)) {}

  private headers(json = false): Record<string, string> {
    const headers: Record<string, string> = json ? { 'Content-Type': 'application/json' } : {}
    if (this.config.token) headers.Authorization = `Bearer ${this.config.token}`
    return headers
  }

  private async get<T>(path: string): Promise<T> {
    const response = await this.fetchImpl(`${this.config.api}${path}`, { headers: this.headers() })
    if (!response.ok) throw new Error(`${path} → ${response.status}`)
    return response.json() as Promise<T>
  }

  private async send<T>(method: string, path: string, body: unknown): Promise<T> {
    const response = await this.fetchImpl(`${this.config.api}${path}`, { method, headers: this.headers(true), body: JSON.stringify(body) })
    if (!response.ok) throw new Error(`${method} ${path} → ${response.status}`)
    return response.json() as Promise<T>
  }

  /**
   * Everything the room shows; `sprintId` is what the stream then follows.
   * A `reload` brings only what the board and the inbox can change (the
   * tasks and the runs), so the log and the agents' states the stream built
   * since the room opened stay.
   */
  async load(reload = false): Promise<{ world: Partial<World>; sprintId: number | undefined }> {
    const { companyId } = this.config
    const sprints = await this.get<ApiSprint[]>(`/v1/sprints?company_id=${companyId}`)
    const sprint = pickSprint(sprints, this.config.sprintId)
    const [board, inbox, runList] = await Promise.all([
      sprint ? this.get<ApiBoard>(`/v1/sprints/${sprint.id}/board`) : Promise.resolve(undefined),
      this.get<ApiInbox>(`/v1/inbox?company_id=${companyId}`),
      this.get<{ agent_runs: ApiRun[] }>(`/v1/agent_runs?company_id=${companyId}&exclude_in_house=true&limit=100`),
    ])
    const runs = runList.agent_runs
    const pending = runs.filter(run => !run.latest_approval).slice(0, RUNS_WITH_OBSERVATIONS)
    const verifications: Record<number, ApiVerification | undefined> = {}
    await Promise.all(
      pending.map(async run => {
        const { verifications: list } = await this.get<{ verifications: ApiVerification[] }>(`/v1/agent_runs/${run.id}/verifications?company_id=${companyId}`)
        verifications[run.id] = list.at(-1)
      }),
    )
    const tasks = board ? tasksFromBoard(board, inbox) : []
    const world: Partial<World> = {
      mode: 'live',
      company: `Company ${companyId}`,
      sprint: sprint ? sprint.sprint_name : 'No sprint yet',
      tasks,
      runs: runsFromApi(runs, verifications),
    }
    if (reload) return { sprintId: sprint?.id, world }
    // The conversation between roles is context, not what the room needs: a
    // backend without it still opens.
    const messages = sprint
      ? await this.get<{ messages: ApiMessage[] }>(`/v1/messages?company_id=${companyId}&sprint_id=${sprint.id}&limit=${MESSAGES_IN_LOG}`).then(response => response.messages, () => [])
      : []
    return {
      sprintId: sprint?.id,
      world: { ...world, agents: agentsFromTasks(tasks), log: logFromMessages(messages).sort((a, b) => a.at - b.at) },
    }
  }

  /** A person's decision on a run, recorded by the backend as an approval. */
  decideRun(runId: number, decision: Decision): Promise<unknown> {
    return this.send('POST', `/v1/agent_runs/${runId}/approve`, {
      company_id: this.config.companyId,
      approver_name: this.config.person,
      decision,
      ...(decision === 'rejected' ? { feedback: 'Rejected from Glassroom VR.', rejection_category: 'other' } : {}),
    })
  }

  /** A blocked task sent back to work, as the Review page does it. */
  sendBack(taskId: number): Promise<unknown> {
    return this.send('PATCH', `/v1/backlog/${taskId}`, { status: 'todo', comment: SEND_BACK_COMMENT })
  }

  /** The URL of the sprint's Glassroom stream, with a ticket when the backend hands one out. */
  async streamUrl(sprintId: number): Promise<string> {
    let ticket: string | null = null
    try {
      const response = await this.fetchImpl(`${this.config.api}/v1/sprints/${sprintId}/stream_ticket`, { method: 'POST', headers: this.headers() })
      if (response.ok) ticket = ((await response.json()) as { ticket: string | null }).ticket
    } catch {
      ticket = null
    }
    const params = new URLSearchParams({ sprint_id: String(sprintId) })
    if (ticket) params.set('ticket', ticket)
    return `${this.config.api}/v1/glassroom/stream?${params}`
  }
}
