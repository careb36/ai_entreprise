/**
 * The sprint board's pure helpers (components/KanbanBoard.tsx and
 * components/board/): the five columns, what a move did in the person's
 * language, the QA signals read from a task's messages, and the board's
 * client-side filters and presets.
 */
import type { useTranslation } from 'react-i18next'
import type { BacklogItem } from '../services/api'
import type { BoardMoveResult, CarryResult } from '../services/boardMoves'

export type BoardStatus = BacklogItem['status']
type Translate = ReturnType<typeof useTranslation>['t']

/** The board's columns, left to right: the five statuses a task can have. */
export const BOARD_COLUMNS: readonly { id: BoardStatus; titleKey: string }[] = [
  { id: 'todo', titleKey: 'kanban.todo' },
  { id: 'in_progress', titleKey: 'kanban.inProgress' },
  { id: 'in_review', titleKey: 'kanban.review' },
  { id: 'done', titleKey: 'kanban.done' },
  { id: 'blocked', titleKey: 'kanban.blocked' },
]

export const VALID_STATUSES: BoardStatus[] = BOARD_COLUMNS.map(column => column.id)

/** The cards a Manager may move to another sprint. */
export const MOVABLE_STATUSES: BoardStatus[] = ['blocked', 'todo']

export const QA_REWORK_LIMIT = 2
export const QA_FAILURE_LIMIT = 3

/** What a move to To do did, in the person's language (the backend's reason is English). */
export function describeMove(t: Translate, result: BoardMoveResult): string | null {
  if (result.queued === undefined) return null
  if (result.queued) return t(result.reopened ? 'kanban.moveReopened' : 'kanban.moveQueued')
  if (result.sprint_status === 'paused' || result.sprint_status === 'planned') {
    return t(`kanban.moveWaits.${result.sprint_status}`)
  }
  return t(result.reopened ? 'kanban.moveReopenedWaiting' : 'kanban.moveWaits.dependencies')
}

export function describeCarry(t: Translate, result: CarryResult): string {
  const moved = t('kanban.movedToSprint', { name: result.sprint_name })
  if (result.queued) return moved
  const waits = result.sprint_status === 'paused' || result.sprint_status === 'planned'
    ? t(`kanban.moveWaits.${result.sprint_status}`)
    : t('kanban.moveWaits.dependencies')
  return `${moved} ${waits}`
}

export function findColumnIdByItemId(columns: Record<string, BacklogItem[]>, itemId: number): BoardStatus | undefined {
  return BOARD_COLUMNS.find(column => (columns[column.id] ?? []).some(item => item.id === itemId))?.id
}

/** The column beside *status* in direction *step* (-1 left, +1 right), or null at an edge. */
export function adjacentStatus(status: BoardStatus, step: -1 | 1): BoardStatus | null {
  const index = VALID_STATUSES.indexOf(status)
  return VALID_STATUSES[index + step] ?? null
}

/**
 * QA's rework requests and failed reviews, counted from the task's messages.
 * Messages are what agents and the system said: claims, not observations.
 */
export function getQaMetrics(item: BacklogItem): { reworks: number; failures: number } {
  const comments = Array.isArray(item.comments) ? item.comments : []
  let reworks = 0
  let failures = 0
  for (const comment of comments) {
    const author = String(comment.author || '').toLowerCase()
    const text = String(comment.text || '').toLowerCase()
    if (author !== 'qa' && author !== 'system') continue
    if (text.startsWith('needs work:') || text.includes('requested rework')) reworks += 1
    if (text.includes('qa review failed') || text.includes('cannot review')) failures += 1
  }
  return { reworks, failures }
}

/** Whether what was observed says a person must act: waiting for one, or a protected path held. */
export function needsPerson(item: BacklogItem): boolean {
  const observed = item.observed
  return Boolean(observed && (observed.waits_for_person || observed.protected_paths.length > 0))
}

// ── Filters and presets ───────────────────────────────────────────────────────

/** Built-in views of the board, each read from what was observed or from the plan. */
export const BOARD_PRESETS = ['all', 'needsPerson', 'highRisk', 'waiting'] as const
export type BoardPreset = typeof BOARD_PRESETS[number]

export interface BoardFilters {
  search: string
  role: string
  preset: BoardPreset
}

export const EMPTY_FILTERS: BoardFilters = { search: '', role: '', preset: 'all' }

export function filtersActive(filters: BoardFilters): boolean {
  return filters.search.trim() !== '' || filters.role !== '' || filters.preset !== 'all'
}

export function matchesPreset(item: BacklogItem, preset: BoardPreset): boolean {
  switch (preset) {
    case 'needsPerson': return needsPerson(item)
    case 'highRisk': return item.observed?.risk_tier === 'high'
    case 'waiting': return (item.waiting_for?.length ?? 0) > 0
    default: return true
  }
}

export function matchesFilters(item: BacklogItem, filters: BoardFilters): boolean {
  const query = filters.search.trim().toLowerCase().replace(/^#/, '')
  if (query && !item.title.toLowerCase().includes(query) && String(item.id) !== query) return false
  if (filters.role && (item.assignee ?? '') !== filters.role) return false
  return matchesPreset(item, filters.preset)
}

/** The board's columns with only the cards that pass *filters*. */
export function filterColumns(columns: Record<string, BacklogItem[]>, filters: BoardFilters): Record<string, BacklogItem[]> {
  if (!filtersActive(filters)) return columns
  return Object.fromEntries(
    Object.entries(columns).map(([id, items]) => [id, items.filter(item => matchesFilters(item, filters))]),
  )
}

/** The roles the sprint's tasks are assigned to, sorted. */
export function rolesOf(columns: Record<string, BacklogItem[]>): string[] {
  const roles = new Set<string>()
  for (const items of Object.values(columns)) for (const item of items) if (item.assignee) roles.add(item.assignee)
  return [...roles].sort()
}

// ── Activity ──────────────────────────────────────────────────────────────────

/** One message on a task: what an agent, the system or a person said. A claim, never an observation. */
export interface BoardMessage {
  key: string
  author: string
  text: string
  createdAt: string
  item: BacklogItem
}

/** The sprint's latest task messages, newest first, read from the board already loaded. */
export function latestMessages(columns: Record<string, BacklogItem[]>, limit = 40): BoardMessage[] {
  const entries: BoardMessage[] = []
  for (const items of Object.values(columns)) {
    for (const item of items) {
      (Array.isArray(item.comments) ? item.comments : []).forEach((comment, index) => {
        entries.push({
          key: `${item.id}-${index}`,
          author: String(comment.author || ''),
          text: String(comment.text || ''),
          createdAt: comment.created_at,
          item,
        })
      })
    }
  }
  return entries
    .sort((a, b) => String(b.createdAt ?? '').localeCompare(String(a.createdAt ?? '')))
    .slice(0, limit)
}

// ── Presentation ──────────────────────────────────────────────────────────────

/** Two letters for a role: its capitals ("BackendDeveloper" → "BD"), or its first two letters. */
export function roleInitials(role: string): string {
  const capitals = role.replace(/[^A-Z]/g, '')
  if (capitals.length >= 2) return capitals.slice(0, 2)
  return role.slice(0, 2).toUpperCase()
}

const AVATAR_TONES = [
  'bg-sky-900/70 text-sky-200 ring-sky-700/60',
  'bg-violet-900/70 text-violet-200 ring-violet-700/60',
  'bg-emerald-900/70 text-emerald-200 ring-emerald-700/60',
  'bg-rose-900/70 text-rose-200 ring-rose-700/60',
  'bg-amber-900/70 text-amber-200 ring-amber-700/60',
  'bg-teal-900/70 text-teal-200 ring-teal-700/60',
  'bg-fuchsia-900/70 text-fuchsia-200 ring-fuchsia-700/60',
  'bg-indigo-900/70 text-indigo-200 ring-indigo-700/60',
]

/** The same tone for the same role on every card. */
export function roleTone(role: string): string {
  let hash = 0
  for (const char of role) hash = (hash * 31 + char.charCodeAt(0)) >>> 0
  return AVATAR_TONES[hash % AVATAR_TONES.length]
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 365 * 24 * 3600], ['month', 30 * 24 * 3600], ['week', 7 * 24 * 3600],
  ['day', 24 * 3600], ['hour', 3600], ['minute', 60],
]

/** "12 minutes ago" in *language*; "now" under a minute; null for a date that does not parse. */
export function relativeTime(iso: string | undefined, language: string, now = Date.now()): string | null {
  if (!iso) return null
  // The backend writes naive UTC timestamps ("2026-10-01 00:00:00").
  const text = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso.replace(' ', 'T')}Z`
  const then = Date.parse(text)
  if (Number.isNaN(then)) return null
  const seconds = Math.round((then - now) / 1000)
  const format = new Intl.RelativeTimeFormat(language, { numeric: 'auto', style: 'short' })
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit)
  }
  return format.format(0, 'minute')
}
