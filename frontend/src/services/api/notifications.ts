import type { Http } from './client'

/** The kinds of personal notification the backend writes (issue #683). */
export type NotificationKind =
  | 'sso_break_glass_exclusion'
  | 'sso_break_glass_lifted'
  | 'sso_domain_lapsed'
  | 'review_requested'
  | 'integration_waiting'

export type NotificationState = 'unread' | 'snoozed' | 'all'

/**
 * One notice to the signed-in person: a kind, a reference to its subject and
 * the ids its link needs. It never carries the subject's text; the page it
 * links to shows that, to whoever may see it.
 */
export interface PersonalNotification {
  id: number
  company_id: number
  kind: NotificationKind | string
  security: boolean
  subject: { type: string; id: number }
  context: Record<string, number>
  created_at: string | null
  read_at: string | null
  snoozed_until: string | null
}

export interface NotificationList {
  notifications: PersonalNotification[]
  unread: number
  kinds: string[]
}

/** What the person wants for one kind; a security notice is always shown in the app. */
export interface NotificationPreference {
  kind: NotificationKind | string
  security: boolean
  in_app: boolean
  email: boolean
  in_app_locked: boolean
}

export interface NotificationPreferences {
  preferences: NotificationPreference[]
  /** Whether the deployment can send email at all (SMTP configured). */
  email_available: boolean
}

export const notificationEndpoints = (http: Http) => ({
  getNotifications: (state: NotificationState = 'unread') =>
    http.get<NotificationList>('/v1/notifications', { state }),

  getNotificationCount: () => http.get<{ unread: number }>('/v1/notifications/count'),

  markNotificationRead: (id: number, read = true) =>
    http.post<PersonalNotification>(`/v1/notifications/${id}/read`, { read }),

  markAllNotificationsRead: () => http.post<{ read: number }>('/v1/notifications/read', {}),

  /** Hide a notice until `until` (an ISO date); `null` brings it back now. */
  snoozeNotification: (id: number, until: string | null) =>
    http.post<PersonalNotification>(`/v1/notifications/${id}/snooze`, { until }),

  getNotificationPreferences: () => http.get<NotificationPreferences>('/v1/notifications/preferences'),

  setNotificationPreference: (kind: string, choice: { in_app: boolean; email: boolean }) =>
    http.put<NotificationPreference>(`/v1/notifications/preferences/${encodeURIComponent(kind)}`, choice),
})
