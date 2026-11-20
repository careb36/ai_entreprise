import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, isClientError, type NotificationState } from '../services/api'

const root = ['notifications'] as const
const countKey = [...root, 'count'] as const
const preferencesKey = [...root, 'preferences'] as const

/** The signed-in person's unread count, for the bell; it stops asking on a 4xx. */
export function useNotificationCount(enabled: boolean) {
  return useQuery({
    queryKey: countKey,
    queryFn: () => api.getNotificationCount(),
    enabled,
    refetchInterval: query => isClientError(query.state.error) ? false : 30_000,
    retry: false,
  })
}

/** The person's own notices in one state, newest first. */
export function useNotifications(state: NotificationState) {
  return useQuery({ queryKey: [...root, 'list', state], queryFn: () => api.getNotifications(state) })
}

export function useNotificationPreferences() {
  return useQuery({ queryKey: preferencesKey, queryFn: () => api.getNotificationPreferences() })
}

function useRefresh() {
  const client = useQueryClient()
  return () => client.invalidateQueries({ queryKey: root })
}

export function useMarkNotificationRead() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: ({ id, read }: { id: number; read: boolean }) => api.markNotificationRead(id, read),
    onSuccess: refresh,
  })
}

export function useMarkAllNotificationsRead() {
  const refresh = useRefresh()
  return useMutation({ mutationFn: () => api.markAllNotificationsRead(), onSuccess: refresh })
}

export function useSnoozeNotification() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: ({ id, until }: { id: number; until: string | null }) => api.snoozeNotification(id, until),
    onSuccess: refresh,
  })
}

export function useSetNotificationPreference() {
  const refresh = useRefresh()
  return useMutation({
    mutationFn: ({ kind, in_app, email }: { kind: string; in_app: boolean; email: boolean }) =>
      api.setNotificationPreference(kind, { in_app, email }),
    onSuccess: refresh,
  })
}
