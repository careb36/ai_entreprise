/**
 * What the room shows and what a person can do in it, with no rendering.
 *
 * The same rules as the web product: a task's status and the evidence are
 * observations; an agent's suggestion is a claim shown beside them; only the
 * person's pinch records a decision.
 */

export type TaskStatus = 'todo' | 'in_progress' | 'in_review' | 'done' | 'blocked'

export const COLUMNS: { status: TaskStatus; label: string }[] = [
  { status: 'todo', label: 'To do' },
  { status: 'in_progress', label: 'In progress' },
  { status: 'in_review', label: 'In review' },
  { status: 'done', label: 'Done' },
  { status: 'blocked', label: 'Blocked' },
]

export interface Task {
  id: number
  title: string
  status: TaskStatus
  assignee?: string
  /** Why the task waits for a person (blocked tasks only). */
  reason?: string
  /**
   * Whether a blocked task waits for a person, as the backend's inbox says
   * (the worker still retries some blocked tasks, and some were carried
   * over); unknown counts as yes.
   */
  forPerson?: boolean
  /** Frozen by an operator: shown, but not sent back from here. */
  frozen?: boolean
  /** Rework requests so far. */
  rework: number
}

export type AgentState = 'idle' | 'active' | 'waiting' | 'error'

export interface Agent {
  role: string
  state: AgentState
  activity: string
}

export type Verdict = 'verified' | 'refuted' | 'unverifiable' | 'ingested'
export type Decision = 'approved' | 'rejected'

export interface Run {
  id: number
  producer: string
  externalRef: string
  /** The Verifier's deterministic verdict: an observation. */
  verdict: Verdict
  observations: string[]
  /** The Reviewer agent's suggestion: a claim, never a decision. */
  suggestion?: { decision: Decision; reason: string }
  /** A person's decision, once recorded. */
  decision?: { by: string; decision: Decision }
}

export interface LogEntry {
  at: number
  text: string
}

export type Selection =
  | { kind: 'task'; id: number }
  | { kind: 'run'; id: number }
  | { kind: 'agent'; role: string }

export interface World {
  mode: 'demo' | 'live'
  company: string
  sprint: string
  tasks: Task[]
  agents: Agent[]
  runs: Run[]
  selected: Selection | null
  log: LogEntry[]
}

export type Action =
  | { type: 'select'; selection: Selection | null }
  | { type: 'decide_run'; id: number; decision: Decision; by: string }
  | { type: 'send_back'; id: number; by: string }
  | { type: 'event'; text: string; at?: number }
  | { type: 'agent'; agent: Agent }
  | { type: 'task'; task: Pick<Task, 'id'> & Partial<Task> }
  | { type: 'run'; run: Run }
  | { type: 'load'; world: Partial<World> }

export const LOG_LIMIT = 40

export function tasksByStatus(tasks: Task[]): Record<TaskStatus, Task[]> {
  const columns = { todo: [], in_progress: [], in_review: [], done: [], blocked: [] } as Record<TaskStatus, Task[]>
  for (const task of tasks) columns[task.status].push(task)
  return columns
}

/** Runs of other producers nobody decided: what the inbox holds. */
export function pendingRuns(world: World): Run[] {
  return world.runs.filter(run => !run.decision)
}

/** Tasks blocked until a person acts. */
export function blockedForPerson(world: World): Task[] {
  return world.tasks.filter(task => task.status === 'blocked' && task.forPerson !== false)
}

export function selectedTask(world: World): Task | undefined {
  const selection = world.selected
  return selection?.kind === 'task' ? world.tasks.find(task => task.id === selection.id) : undefined
}

export function selectedRun(world: World): Run | undefined {
  const selection = world.selected
  return selection?.kind === 'run' ? world.runs.find(run => run.id === selection.id) : undefined
}

export function selectedAgent(world: World): Agent | undefined {
  const selection = world.selected
  return selection?.kind === 'agent' ? world.agents.find(agent => agent.role === selection.role) : undefined
}

function sameSelection(a: Selection | null, b: Selection | null): boolean {
  if (a === null || b === null) return a === b
  if (a.kind !== b.kind) return false
  return a.kind === 'agent' ? a.role === (b as { role: string }).role : a.id === (b as { id: number }).id
}

function logged(world: World, text: string, at = Date.now()): LogEntry[] {
  return [...world.log, { at, text }].slice(-LOG_LIMIT)
}

/** The agents with one of them in a new state, added when the board did not list it. */
export function withAgent(agents: Agent[], agent: Agent): Agent[] {
  const index = agents.findIndex(candidate => candidate.role === agent.role)
  if (index === -1) return [...agents, agent]
  return agents.map((candidate, position) => (position === index ? agent : candidate))
}

export function reduce(world: World, action: Action): World {
  switch (action.type) {
    case 'select':
      // Pinching the selected card again puts it down.
      return { ...world, selected: sameSelection(world.selected, action.selection) ? null : action.selection }
    case 'decide_run': {
      const run = world.runs.find(candidate => candidate.id === action.id)
      if (!run || run.decision) return world
      const decided = { ...run, decision: { by: action.by, decision: action.decision } }
      const agreed = run.suggestion ? (run.suggestion.decision === action.decision ? ', as the Reviewer agent suggested' : ', against the Reviewer agent\'s suggestion') : ''
      return {
        ...world,
        runs: world.runs.map(candidate => (candidate.id === run.id ? decided : candidate)),
        selected: world.selected?.kind === 'run' && world.selected.id === run.id ? null : world.selected,
        log: logged(world, `${action.by} ${action.decision} ${run.externalRef}${agreed}`),
      }
    }
    case 'send_back': {
      const task = world.tasks.find(candidate => candidate.id === action.id)
      if (!task || task.status !== 'blocked' || task.frozen) return world
      // Back to the top of the board, as the backend puts it.
      // The rework count is the backend's (its review requests); a send-back is not one.
      const back: Task = { ...task, status: 'todo', reason: undefined, forPerson: undefined }
      return {
        ...world,
        tasks: world.tasks.map(candidate => (candidate.id === task.id ? back : candidate)),
        selected: world.selected?.kind === 'task' && world.selected.id === task.id ? null : world.selected,
        log: logged(world, `${action.by} sent "${task.title}" back to work`),
      }
    }
    case 'event':
      return { ...world, log: logged(world, action.text, action.at) }
    case 'agent':
      return { ...world, agents: withAgent(world.agents, action.agent) }
    case 'task': {
      // What the board reports about a task: fields on an existing card, or a new card.
      const known = world.tasks.some(task => task.id === action.task.id)
      const tasks = known
        ? world.tasks.map(task => (task.id === action.task.id ? { ...task, ...action.task } : task))
        : [...world.tasks, { title: `Task ${action.task.id}`, status: 'todo' as TaskStatus, rework: 0, ...action.task }]
      return { ...world, tasks }
    }
    case 'run': {
      const known = world.runs.some(run => run.id === action.run.id)
      return { ...world, runs: known ? world.runs.map(run => (run.id === action.run.id ? action.run : run)) : [...world.runs, action.run] }
    }
    case 'load':
      return { ...world, ...action.world }
  }
}
