import { useMutation, useQuery } from '@tanstack/react-query'
import { api, setCsrfToken } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'

/** Whether the signed-in person has a password, and the rule a new one follows. */
export function usePasswordRule() {
  return useQuery({
    queryKey: ['auth', 'me', 'password'] as const,
    queryFn: async () => {
      const me = await api.me()
      return { hasPassword: me.has_password !== false, minLength: me.password_min_length ?? 12 }
    },
  })
}

/**
 * Change the password. The backend ends every session and starts a new one
 * for this browser: its CSRF value and lifetime replace the old ones here.
 */
export function useChangePassword() {
  return useMutation({
    mutationFn: ({ current, next }: { current: string; next: string }) => api.changePassword(current, next),
    onSuccess: answer => {
      if (answer.csrf_token) setCsrfToken(answer.csrf_token)
      if (answer.session_expires_in) {
        useAuthStore.setState({ expiresAt: Date.now() + answer.session_expires_in * 1000 })
      }
    },
  })
}
