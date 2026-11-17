import type { Http } from './client'

export interface BacklogItem {
  id: number
  sprint_id: number
  title: string
  description?: string
  status: 'todo' | 'in_progress' | 'in_review' | 'done' | 'blocked'
  priority: 'low' | 'medium' | 'high' | 'critical'
  assignee?: string
  comments: { author: string; text: string; created_at: string }[]
  created_at: string
  updated_at: string
  covers?: string[]; kind?: 'acceptance_tests' | null // Criteria ids the task covers; QA's test-writing task of a requirement.
  depends_on?: number[]; waiting_for?: number[] // Ids of sprint tasks to finish first, and those among them not done yet.
  /** What was observed (the Verifier, the task's record), apart from what any agent claimed. */
  observed?: ObservedFacts
}

/** The board's observed facts of a task: never read from a producer's claims. */
export interface ObservedFacts {
  verdict: 'verified' | 'refuted' | 'unverifiable' | null
  risk_tier: 'low' | 'medium' | 'high' | null
  /** The areas the Verifier's risk tier named; `grader` is a protected path. */
  risk_areas: string[]
  /** The protected paths the Verifier matched: the delivery waits for a person. */
  protected_paths: string[]
  waits_for_person: boolean
}

export interface SprintBoard {
  sprint: {
    id: number
    company_id: number
    sprint_name: string
    status: string
    goal?: string
    start_date: string
    scope_size: string
    item_limit: number
    specification_id?: number | null; team?: string[] | null; team_source?: string // The roles its tasks may go to (null: every hired role), and who chose them.
  }
  columns: Record<string, BacklogItem[]>
  total: number
}

export interface SprintSummary {
  id: number
  sprint_name: string
  status: string
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
  /** Tasks a console assistant advised on and tasks none did: how many got done and how many rework requests each drew. */
  console_advice: ConsoleAdvice
  /** The same split for the Architect's briefs; a briefed task's rework counts from its first brief on. */
  architect_advice: ConsoleAdvice
}

export interface AdviceGroup {
  total: number
  done: number
  rework: number
}

export interface ConsoleAdvice {
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

export interface SprintPlanResponse {
  sprint_name: string
  goal: string
  scope_size: 'S' | 'M' | 'L'
  specification_id?: number | null
  tasks: PlannedSprintTask[]
  /** Criteria no task covers; the team the PO suggests (why, per role) among the company's hired roles. */
  uncovered?: string[]; suggested_team?: string[]; team_reasons?: Record<string, string>; hired_roles?: string[]; team_selection?: 'suggested' | 'automatic'
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

  /** A short-lived ticket for the sprint's Glassroom stream; null when no login is required. */
  getStreamTicket: (sprintId: string) =>
    api.post<{ ticket: string | null; expires_in: number }>(
      `/v1/sprints/${encodeURIComponent(sprintId)}/stream_ticket`,
    ),

  updateBacklogItem: (itemId: number, data: { status?: string; priority?: string; assignee?: string; comment?: string }) =>
    api.patch<BacklogItem>(`/v1/backlog/${itemId}`, data),

  startSprint: (data: { company_id: number; sprint_name: string; start_date: string; scope_size: string; goal?: string; backlog_items: any[]; carry_over_ids?: number[]; specification_id?: number; team?: string[]; suggested_team?: string[] }) =>
    api.post<{ sprint_id: number; backlog_item_ids?: number[]; sprint_url?: string }>('/v1/start_sprint', data),

  planSprint: (data: { company_id: number; prompt?: string; scope_size: 'S' | 'M' | 'L'; specification_id?: number; team?: string[] }) =>
    api.post<SprintPlanResponse>('/v1/plan_sprint', data),

  planSelfImprovementSprint: (data: { company_id: number; scope_size: 'S' | 'M' | 'L'; team?: string[] }) =>
    api.post<SprintPlanResponse>('/v1/plan_self_improvement_sprint', data),

  generate: (data: { company_id: number; story: string; constraints?: string; interaction_id?: string }) =>
    api.post<any>('/v1/generate', data),
})
