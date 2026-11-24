import type { Http } from './client'
import type { RunWindowHold } from './runWindow'

export interface BacklogItem {
  id: number
  sprint_id: number
  title: string
  description?: string
  status: 'todo' | 'in_progress' | 'in_review' | 'done' | 'blocked' | 'discarded'
  priority: 'low' | 'medium' | 'high' | 'critical'
  assignee?: string
  /** `by`/`identified`: who made a person's change, from the signed-in session (issue #767). */
  comments: { author: string; text: string; created_at: string; by?: string | null; identified?: boolean }[]
  created_at: string
  updated_at: string
  covers?: string[]; kind?: 'acceptance_tests' | null // Criteria ids the task covers; QA's test-writing task of a requirement.
  depends_on?: number[]; waiting_for?: number[] // Ids of sprint tasks to finish first, and those among them not done yet.
  /** What was observed (the Verifier, the task's record), apart from what any agent claimed. */
  observed?: ObservedFacts
  /** What the producer declared that bears on a person's review: a claim, never an observation. */
  reported?: ReportedFacts
}

/** The board's observed facts of a task: never read from a producer's claims. */
export interface ObservedFacts {
  /** The latest task-worker run the facts belong to. */
  run_id: number | null
  verdict: 'verified' | 'refuted' | 'unverifiable' | null
  risk_tier: 'low' | 'medium' | 'high' | null
  /** The areas the Verifier's risk tier named; `grader` is a protected path. */
  risk_areas: string[]
  /** The protected paths the daemon saw the delivery write. */
  protected_paths: string[]
  /** Only a person releases what holds the task (a person, a risk hold or a freeze); a dependency wait is not one. */
  waits_for_person: boolean
  /** What holds the task back, from its record (`services/tasks/task_hold.py`); null when nothing does. */
  hold?: TaskHold | null
}

/** What holds a task back, read from its record by the backend. */
export type TaskHoldKind = 'person' | 'dependency' | 'retry_pending' | 'review_pending' | 'frozen' | 'risk_hold' | 'run_window'
export interface TaskHold {
  kind: TaskHoldKind
  /** The latest verdict's text (person, retry_pending) or the hold's note (risk_hold). */
  reason?: string
  /** The dependencies not done yet (dependency). */
  waiting_for?: number[]
}

/** Why a paused sprint is paused, from the reason the code that paused it wrote (`services/loop/live_state.py`). */
export type PauseKind = 'person' | 'no_credit' | 'nothing_done' | 'budget' | 'acknowledgement' | 'daemon' | 'other'

/** One agent run in flight now: an assignee's run of a claimed task, or QA's review of a delivery. */
export interface LiveRun {
  role: string
  task_id: number
  task_title: string | null
  kind: 'task' | 'review'
  /** When this process started it; null for a run seen only through the task's lock (another replica). */
  since: string | null
}

/** The Live page's state of a sprint, read from the records (`GET /v1/sprints/{id}/live_state`). */
export interface SprintLiveState {
  sprint_id: number
  status: string
  /** Active or needs_split: only then can anyone be working. */
  running: boolean
  pause_reason: string | null
  pause_kind: PauseKind | null
  /** When a completed or cancelled sprint closed (its sealed progress snapshot); naive UTC. */
  closed_at: string | null
  working: LiveRun[]
  tiles: {
    active_agents: number
    agent_failures: number
    blocked_tasks: number
    open_reworks: number
    last_agent_event_at: string | null
    daemon: { state: 'online' | 'stale' | 'offline' | 'not_observed'; seconds_since_heartbeat: number | null }
    previous_sprint: { id: number; name: string; done: number; total: number } | null
  }
}

/** The board's reported facts of a task: what the producer declared. */
export interface ReportedFacts {
  /** The protected paths only the producer declared (the Verifier did not see them written). */
  protected_paths: string[]
}

/** Where a sprint's latest integration stands, as the backend recorded it (`services/release/integration_state.py`). */
export type IntegrationState = 'none' | 'integrating' | 'integrated' | 'failed'

export interface SprintBoard {
  sprint: {
    id: number
    company_id: number
    sprint_name: string
    status: string
    integration_state?: IntegrationState; integration_status?: string | null
    pause_reason?: string | null; pause_kind?: PauseKind | null // Why it is paused, as written and as a kind.
    goal?: string
    start_date: string
    scope_size: string
    item_limit: number
    specification_id?: number | null; team?: string[] | null; team_source?: string // The roles its tasks may go to (null: every hired role), and who chose them.
    /** A Manager's bypass of the run window, and the hold while the window keeps its work back (issue #736). */
    urgent?: boolean; run_window?: RunWindowHold | null
    /** The objective the person wrote, whole; the planner's fallback as the backend recorded it. */
    objective?: string | null; planner_fallback?: PlannerFallback | null
  }
  columns: Record<string, BacklogItem[]>
  total: number
}

export interface SprintSummary {
  id: number
  sprint_name: string
  status: string
  integration_state?: IntegrationState; integration_status?: string | null
  goal: string | null
  start_date: string | null
  scope_size: string
  specification_id?: number | null; team?: string[] | null; team_source?: string
  total: number
  done: number
  /** Tasks blocked on the board, and rework requests over the sprint: the numbers procedures are judged by. */
  blocked: number
  rework: number
  /** Tasks carried over from an earlier sprint, how many got done, and the same for those the ScrumMaster briefed. */
  second_attempts: SecondAttempts
  /** Tasks the Architect briefed and tasks it did not: how many got done and how many rework requests each drew; a briefed task's rework counts from its first brief on. */
  architect_advice: AdviceTally
}

export interface AdviceGroup {
  total: number
  done: number
  rework: number
}

export interface AdviceTally {
  advised: AdviceGroup
  unadvised: AdviceGroup
}

export interface SecondAttempts {
  total: number
  done: number
  briefed: number
  briefed_done: number
}

export interface PlannedSprintTask {
  title: string
  description: string
  priority: 'low' | 'medium' | 'high' | 'critical'
  assignee: string
  covers?: string[] // Criteria ids, when planned from a specification.
  depends_on?: number[]; kind?: 'acceptance_tests' | null // Positions in the plan's `tasks` to finish first; QA's test-writing task.
}

/** Why the planner's model gave no usable answer, as the backend observed it. */
export interface PlannerFallback {
  reason: 'no_model' | 'model_error' | 'unparseable' | 'too_few_tasks' | 'unexpected_error' | string
  detail: string
  recorded_at?: string | null
}

/** What the PO says is already done: its claim, in its words (`already_done.claim_of`). */
export interface AlreadyDonePart {
  part: string
  evidence: string
  paths: string[]
}

/** What the backend observed of the PO's planning reads: never the PO's word. */
export interface AlreadyDoneObserved {
  files_read: { path: string; sha256: string; bytes?: number | null; tool: string; round?: number | null }[]
  searches: { tool: string; pattern?: string | null; path?: string | null; match_count?: number | null }[]
  claimed_paths: { path: string; read: boolean; files: { path: string; sha256: string }[] }[]
}

export interface SprintPlanResponse {
  sprint_name: string
  goal: string
  scope_size: 'S' | 'M' | 'L'
  specification_id?: number | null
  tasks: PlannedSprintTask[]
  /** Criteria no task covers; the team the PO suggests (why, per role) among the company's hired roles. */
  uncovered?: string[]; suggested_team?: string[]; team_reasons?: Record<string, string>; hired_roles?: string[]; team_selection?: 'suggested' | 'automatic'
  /** The person's objective, whole; a fallback plan's reason and the id of the backend's record of it; items the scope left out. */
  objective?: string | null; planner_fallback?: PlannerFallback | null; plan_id?: string | null; unplanned_items?: string[]
  /** The PO's claim of what is already done, apart from the platform's observations of its reads. */
  already_done_claim?: AlreadyDonePart[] | null; already_done_observed?: AlreadyDoneObserved | null
}

export const sprintEndpoints = (api: Http) => ({
  pauseSprint: (sprintId: number) =>
    api.post<{ status: string }>(`/v1/sprints/${sprintId}/pause`, {}),

  resumeSprint: (sprintId: number) =>
    api.post<{ status: string }>(`/v1/sprints/${sprintId}/resume`, {}),

  /** Re-queues the sprint's todo items for the agents (Manager). 409 while paused. */
  dispatchSprint: (sprintId: number) =>
    api.post<{ dispatched: number; sprint_id?: number; message?: string }>(`/v1/sprints/${sprintId}/dispatch`, {}),

  // Sprints & Backlog
  listSprints: (companyId: number) =>
    api.get<SprintSummary[]>('/v1/sprints', { company_id: companyId }),

  getSprintBoard: (sprintId: number) =>
    api.get<SprintBoard>(`/v1/sprints/${sprintId}/board`),

  /** Who works on the sprint now and its health tiles, from the records. */
  getSprintLiveState: (sprintId: number) =>
    api.get<SprintLiveState>(`/v1/sprints/${sprintId}/live_state`),

  /** A short-lived ticket for the sprint's Glassroom stream; null when no login is required. */
  getStreamTicket: (sprintId: string) =>
    api.post<{ ticket: string | null; expires_in: number }>(
      `/v1/sprints/${encodeURIComponent(sprintId)}/stream_ticket`,
    ),

  updateBacklogItem: (itemId: number, data: { status?: string; priority?: string; assignee?: string; comment?: string }) =>
    api.patch<BacklogItem>(`/v1/backlog/${itemId}`, data),

  startSprint: (data: { company_id: number; sprint_name: string; start_date: string; scope_size: string; goal?: string; objective?: string; plan_id?: string; backlog_items: any[]; carry_over_ids?: number[]; specification_id?: number; team?: string[]; suggested_team?: string[] }) =>
    api.post<{ sprint_id: number; backlog_item_ids?: number[]; sprint_url?: string }>('/v1/start_sprint', data),

  planSprint: (data: { company_id: number; prompt?: string; scope_size: 'S' | 'M' | 'L'; specification_id?: number; team?: string[] }) =>
    api.post<SprintPlanResponse>('/v1/plan_sprint', data),

  planSelfImprovementSprint: (data: { company_id: number; scope_size: 'S' | 'M' | 'L'; team?: string[] }) =>
    api.post<SprintPlanResponse>('/v1/plan_self_improvement_sprint', data),

  generate: (data: { company_id: number; story: string; constraints?: string; interaction_id?: string }) =>
    api.post<any>('/v1/generate', data),
})
