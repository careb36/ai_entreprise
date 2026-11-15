import { API_BASE } from './client'

/** The Glassroom's live stream of a sprint's events (Server-Sent Events). */
export type GlassroomStream = EventSource

/**
 * Open the sprint's event stream. An EventSource cannot send an Authorization
 * header, so with authentication required it carries the short-lived ticket
 * of `api.getStreamTicket`; without one the stream is tried as is.
 */
export function openGlassroomStream(sprintId: string, ticket: string | null): GlassroomStream {
  const params = new URLSearchParams({ sprint_id: sprintId })
  if (ticket) params.set('ticket', ticket)
  return new EventSource(`${API_BASE}/v1/glassroom/stream?${params}`)
}
