/**
 * The factory's quality standards: a fixed catalogue, the same for every company
 * (`GET /v1/standards`), shown on the Procedures page.
 */
import { api } from './api'

export interface Standard {
  id: string
  area: string
  /** Roles bound; `*` is every role. */
  roles: string[]
  title: string
  rule: string
  /** How the loop checks it without a model, or null when the rule lives in the prompt alone. */
  checked: string | null
}

export const standardsApi = {
  list: () => api.get<{ standards: Standard[]; areas: string[] }>('/v1/standards'),
}
