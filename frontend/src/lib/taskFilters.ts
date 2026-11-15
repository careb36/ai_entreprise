/**
 * The filter builder's rows and the filter tree the server evaluates (issue
 * #535, `services/contracts/task_filters.py`). A row is what a person edits:
 * a field, an operator and the value as typed; `rowsToFilter` turns the rows
 * into the JSON tree, typed per field, and `filterToRows` reads a saved
 * view's tree back when it is a single group of conditions.
 */
import type {
  FilterCondition, FilterField, FilterGroup, FilterNode, FilterOperator, FilterValue, ViewColumn,
} from '../services/api'

/** The columns a list shows when its view names none (the server's default). */
export const DEFAULT_COLUMNS: readonly ViewColumn[] = ['title', 'status', 'verdict', 'risk_tier', 'producer']

export type FilterJoin = 'and' | 'or'

export interface FilterRow {
  field: FilterField
  op: FilterOperator
  /** As typed or chosen; "none" stands for no value (a task never verified). */
  value: string
}

const EQUALITY: FilterOperator[] = ['eq', 'ne', 'in']
const ORDERED: FilterOperator[] = ['eq', 'ne', 'in', 'gte', 'lte']

/** The operators each field takes, as the server's allowlist says. */
export const FIELD_OPERATORS: Record<FilterField, FilterOperator[]> = {
  status: EQUALITY,
  verdict: EQUALITY,
  risk_tier: ORDERED,
  producer: EQUALITY,
  assignee_role: EQUALITY,
  rework_count: ORDERED,
  waits_for_person: ['eq', 'ne'],
  covers: EQUALITY,
  sprint_id: EQUALITY,
}

export const NONE = 'none'

/** The values a field may take, for the fields with a closed list. */
export const FIELD_CHOICES: Partial<Record<FilterField, string[]>> = {
  status: ['todo', 'in_progress', 'in_review', 'done', 'blocked'],
  verdict: ['verified', 'refuted', 'unverifiable', NONE],
  risk_tier: ['low', 'medium', 'high', NONE],
  waits_for_person: ['true', 'false'],
}

export function emptyRow(field: FilterField = 'status'): FilterRow {
  const choices = FIELD_CHOICES[field]
  return { field, op: 'eq', value: choices ? choices[0] : '' }
}

function typed(field: FilterField, raw: string): FilterValue | undefined {
  const text = raw.trim()
  if (text === '') return undefined
  if (text === NONE && (field === 'verdict' || field === 'risk_tier')) return null
  if (field === 'waits_for_person') return text === 'true'
  if (field === 'rework_count' || field === 'sprint_id') {
    const number = Number(text)
    return Number.isInteger(number) && number >= 0 ? number : undefined
  }
  return text
}

/** The condition a row stands for, or null while it is incomplete. */
export function rowToCondition(row: FilterRow): FilterCondition | null {
  if (row.op === 'in') {
    const values = row.value.split(',').map(part => typed(row.field, part))
    if (values.length === 0 || values.some(v => v === undefined)) return null
    return { field: row.field, op: 'in', value: values as FilterValue[] }
  }
  const value = typed(row.field, row.value)
  return value === undefined ? null : { field: row.field, op: row.op, value }
}

/**
 * The filter tree for *rows* joined by *join*, limited to *sprintId* when
 * given; null when nothing filters.
 */
export function rowsToFilter(rows: FilterRow[], join: FilterJoin, sprintId?: number | null): FilterGroup | null {
  const conditions = rows.map(rowToCondition).filter((c): c is FilterCondition => c !== null)
  const sprint: FilterCondition[] = sprintId ? [{ field: 'sprint_id', op: 'eq', value: sprintId }] : []
  if (conditions.length === 0) return sprint.length ? { and: sprint } : null
  if (join === 'and') return { and: [...sprint, ...conditions] }
  return sprint.length ? { and: [...sprint, { or: conditions }] } : { or: conditions }
}

function isCondition(node: FilterNode): node is FilterCondition {
  return 'field' in node
}

function untyped(value: FilterValue): string {
  return value === null ? NONE : String(value)
}

/** A saved filter as rows, or null when it nests groups the builder does not show. */
export function filterToRows(tree: FilterGroup | Record<string, never> | null): { join: FilterJoin; rows: FilterRow[] } | null {
  if (!tree || Object.keys(tree).length === 0) return { join: 'and', rows: [] }
  const join: FilterJoin = 'and' in tree ? 'and' : 'or'
  const members = 'and' in tree ? tree.and : (tree as { or: FilterNode[] }).or
  if (!members.every(isCondition)) return null
  return {
    join,
    rows: members.map(c => ({
      field: c.field,
      op: c.op,
      value: Array.isArray(c.value) ? c.value.map(untyped).join(',') : untyped(c.value),
    })),
  }
}
