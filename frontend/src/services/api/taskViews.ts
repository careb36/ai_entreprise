import type { Http } from './client'

/**
 * A company's tasks filtered on the evidence, and saved views (issue #535,
 * `routers/task_views.py`). The filter is evaluated on the server from the
 * task projections; the fields come from its allowlist.
 */
export const FILTER_FIELDS = [
  'status', 'verdict', 'risk_tier', 'producer', 'assignee_role',
  'rework_count', 'waits_for_person', 'covers', 'sprint_id',
] as const
export type FilterField = typeof FILTER_FIELDS[number]
export type FilterOperator = 'eq' | 'ne' | 'in' | 'gte' | 'lte'
export type FilterValue = string | number | boolean | null

export interface FilterCondition {
  field: FilterField
  op: FilterOperator
  value: FilterValue | FilterValue[]
}
export type FilterGroup = { and: FilterNode[] } | { or: FilterNode[] }
export type FilterNode = FilterCondition | FilterGroup

export const VIEW_COLUMNS = [
  'title', 'status', 'verdict', 'risk_tier', 'producer', 'assignee_role',
  'rework_count', 'waits_for_person', 'covers', 'sprint_id', 'updated_at',
] as const
export type ViewColumn = typeof VIEW_COLUMNS[number]
export const GROUP_FIELDS = ['status', 'verdict', 'risk_tier', 'producer', 'assignee_role', 'waits_for_person', 'sprint_id'] as const
export type GroupField = typeof GROUP_FIELDS[number]
export type ViewLayout = 'board' | 'list'
export type ViewAccess = 'private' | 'company'

/** One task as the filter read it: the verdict and risk tier are the Verifier's. */
export interface FilteredTask {
  id: number
  sprint_id: number
  title: string
  status: 'todo' | 'in_progress' | 'in_review' | 'done' | 'blocked'
  priority: string
  assignee: string | null
  assignee_role: string | null
  kind: string | null
  covers: string[]
  updated_at: string | null
  verdict: 'verified' | 'refuted' | 'unverifiable' | null
  risk_tier: 'low' | 'medium' | 'high' | null
  producer: string | null
  rework_count: number
  waits_for_person: boolean
}

export interface FilteredTasks {
  tasks: FilteredTask[]
  count: number
  groups?: Record<string, number>
}

export interface TaskQuery {
  companyId: number
  filter?: FilterGroup | null
  viewId?: number | null
  orderBy?: string | null
  groupBy?: GroupField | null
  limit?: number
}

export interface SavedViewSettings {
  name: string
  filters: FilterGroup | Record<string, never>
  group_by: GroupField | null
  order_by: string | null
  layout: ViewLayout
  visible_columns: ViewColumn[]
  access: ViewAccess
  locked: boolean
}

export interface SavedView extends SavedViewSettings {
  id: number
  company_id: number
  owner_name: string
  mine: boolean
  can_edit: boolean
  /** False when its filter names a field no longer allowed: never evaluated in part. */
  valid: boolean
  invalid_reason: { message: string; field: string | null } | null
  updated_by: string | null
  created_at: string | null
  updated_at: string | null
}

export interface SavedViewsResponse {
  views: SavedView[]
  fields: Record<FilterField, FilterOperator[]>
  group_fields: GroupField[]
  order_fields: string[]
  columns: ViewColumn[]
  can_share: boolean
}

export interface ViewPreferences {
  collapsed_groups: string[]
  column_widths: Partial<Record<ViewColumn, number>>
  density: 'comfortable' | 'compact'
}

function taskParams(query: TaskQuery): Record<string, string | number> {
  const params: Record<string, string | number> = { company_id: query.companyId }
  if (query.filter) params.filter = JSON.stringify(query.filter)
  if (query.viewId) params.view_id = query.viewId
  if (query.orderBy) params.order_by = query.orderBy
  if (query.groupBy) params.group_by = query.groupBy
  if (query.limit !== undefined) params.limit = query.limit
  return params
}

export const taskViewEndpoints = (api: Http) => ({
  filterTasks: (query: TaskQuery) => api.get<FilteredTasks>('/v1/tasks', taskParams(query)),

  getSavedViews: (companyId: number) =>
    api.get<SavedViewsResponse>(`/v1/companies/${companyId}/views`),

  createSavedView: (companyId: number, data: Partial<SavedViewSettings> & { name: string }) =>
    api.post<SavedView>(`/v1/companies/${companyId}/views`, data),

  updateSavedView: (viewId: number, data: Partial<SavedViewSettings>) =>
    api.put<SavedView>(`/v1/views/${viewId}`, data),

  deleteSavedView: (viewId: number) => api.delete<void>(`/v1/views/${viewId}`),

  getViewPreferences: (viewId: number) =>
    api.get<ViewPreferences>(`/v1/views/${viewId}/preferences`),

  setViewPreferences: (viewId: number, data: ViewPreferences) =>
    api.put<ViewPreferences>(`/v1/views/${viewId}/preferences`, data),
})
