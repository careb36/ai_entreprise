import type { Http } from './client'

/** One allowed interval in the window's own time zone; an end not after the start runs past midnight. */
export interface RunWindowInterval {
  days: string[]
  start: string
  end: string
}

/**
 * When the company's work that can wait may start (issue #736): a provider's
 * off-peak preset, or intervals, in a named IANA time zone. `null` is off.
 */
export interface RunWindow {
  timezone: string
  preset: string | null
  intervals: RunWindowInterval[]
  holidays: { calendar: string | null; dates: string[] }
}

export interface RunWindowPreset {
  provider: string
  zone: string
  weekdays: string[]
  peak: [string, string][]
  holiday_calendar: string | null
  off_peak_price: string
  source: string
  read_on: string
}

/** Whether the window is open now, when it next opens or changes, and the next 7 days (ISO 8601 in its zone). */
export interface RunWindowStatus {
  run_window: RunWindow | null
  now: string
  open: boolean
  next_open: string | null
  next_change: string | null
  preview: { from: string; to: string }[]
}

export interface RunWindowState extends RunWindowStatus {
  company_id: number
  presets: Record<string, RunWindowPreset>
  calendars: Record<string, { name: string; years: { year: number; source: { url: string; title: string } }[] }>
}

/** A sprint's work held for the window, as the board and the inbox show it. */
export interface RunWindowHold {
  waiting: boolean
  until: string
  until_local: string
  timezone: string
  urgent: boolean
}

export interface SprintUrgency {
  sprint_id: number
  urgent: boolean
  released_tasks: number
  run_window: RunWindowHold | null
}

// Any member reads the window and previews a draft; only an Owner changes it,
// and a Manager marks a sprint urgent. Both changes are audited.
export const runWindowEndpoints = (http: Http) => ({
  getRunWindow: (companyId: number) => http.get<RunWindowState>(`/v1/companies/${companyId}/run_window`),

  setRunWindow: (companyId: number, runWindow: RunWindow | null) =>
    http.put<RunWindowState>(`/v1/companies/${companyId}/run_window`, { run_window: runWindow }),

  previewRunWindow: (companyId: number, runWindow: RunWindow | null) =>
    http.post<RunWindowStatus>(`/v1/companies/${companyId}/run_window/preview`, { run_window: runWindow }),

  setSprintUrgent: (sprintId: number, urgent: boolean) =>
    http.put<SprintUrgency>(`/v1/sprints/${sprintId}/urgent`, { urgent }),
})
