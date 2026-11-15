import type { Http } from './client'

/** The kinds of note a sprint's tasks leave each other (issue #439). */
export const BOARD_NOTE_KINDS = ['decision', 'interface_change', 'schema_change', 'gotcha', 'question'] as const
export type BoardNoteKind = typeof BOARD_NOTE_KINDS[number]
export type BoardNoteStatus = 'active' | 'superseded' | 'marked_wrong'

export interface BoardNoteLinks {
  files: string[]
  symbols: string[]
  requirements: string[]
  modules: string[]
}

/** What the Verifier's records show about one file a note names. */
export interface BoardNoteObservation {
  run_id: number
  backlog_item_id: number | null
}

/**
 * A note on the sprint board. Its fields are its author's claim; `observed`
 * is the platform's own reading of the evidence, kept apart.
 */
export interface BoardNote {
  id: number
  sprint_id: number
  kind: BoardNoteKind
  text: string
  /** A role, or "person" for a note a person wrote. */
  author_role: string
  author_name: string | null
  author_task_id: number | null
  run_id: number | null
  links: BoardNoteLinks
  supersedes_id: number | null
  status: BoardNoteStatus
  status_by: string | null
  status_at: string | null
  status_reason: string | null
  created_at: string | null
  /** Per file the note names: the latest verified run that wrote it, or null. */
  observed: { files: Record<string, BoardNoteObservation | null> }
}

export interface BoardNoteMetrics {
  notes: { total: number; active: number; superseded: number; marked_wrong: number; by_kind: Record<string, number> }
  rework: number
  /** Rework requests that say they contradict another task of the same sprint. */
  contradiction_rework: number
}

export interface BoardNotesResponse {
  notes: BoardNote[]
  metrics: BoardNoteMetrics
  kinds: BoardNoteKind[]
}

export interface BoardNoteCreate {
  kind: BoardNoteKind
  text: string
  files?: string[]
  symbols?: string[]
  requirements?: string[]
  modules?: string[]
  supersedes_id?: number | null
}

export const boardNoteEndpoints = (api: Http) => ({
  getBoardNotes: (sprintId: number) =>
    api.get<BoardNotesResponse>(`/v1/sprints/${sprintId}/board_notes`),

  addBoardNote: (sprintId: number, data: BoardNoteCreate) =>
    api.post<BoardNote>(`/v1/sprints/${sprintId}/board_notes`, data),

  markBoardNoteWrong: (noteId: number, reason: string) =>
    api.post<BoardNote>(`/v1/board_notes/${noteId}/mark_wrong`, { reason: reason || null }),
})
