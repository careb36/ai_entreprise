/**
 * A sprint's lifecycle events in the order they were recorded
 * (`GET /v1/sprints/{id}/events`, Viewer; `services/tasks/events.py`): each
 * event's task, type, payload and time, as the backend stored them.
 */
import { useQuery } from '@tanstack/react-query'
import { api } from './api'

export interface SprintEvent {
  id: number
  task_id: number | null
  event_type: string
  payload: Record<string, unknown> | null
  created_at: string | null
}

export const sprintEventsApi = {
  list: (sprintId: number) => api.get<{ sprint_id: number; events: SprintEvent[] }>(`/v1/sprints/${sprintId}/events`),
}

export function useSprintEvents(sprintId: number, enabled: boolean) {
  return useQuery({ queryKey: ['sprint-events', sprintId], queryFn: () => sprintEventsApi.list(sprintId), enabled })
}
