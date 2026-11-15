import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import {
  api,
  setCsrfToken,
  setSessionHooks,
  type AuthProfile,
  type AuthUser,
  type CompanyMembership,
  type LoginResponse,
} from '../services/api'
import { useAppStore } from './useAppStore'
import { queryClient } from '../lib/queryClient'

/**
 * The signed-in person, as the page may know it. The session's credentials
 * are HttpOnly cookies the backend sets and the page never reads (issue #524,
 * docs/design/SESSIONS.md): nothing here is a credential, and only `user`,
 * `memberships` and `expiresAt` (when the session ends however often it is
 * refreshed) are kept across reloads.
 */
interface AuthState {
  /** When the session ends at the latest (ms since the epoch); null when signed out. */
  expiresAt: number | null
  /** Changes on every sign-in and sign-out, so a late answer from an older session is dropped. */
  sessionEpoch: number
  user: AuthUser | null
  memberships: CompanyMembership[]
  meLoaded: boolean
  loadingMe: boolean
  profileError: string | null
  login: (email: string, password: string) => Promise<void>
  /** Trades the one-time code the SSO callback handed over for a session. */
  loginWithSso: (code: string) => Promise<void>
  loadMe: () => Promise<void>
  /** Signs out here and on the server (the session is revoked). */
  logout: () => void
  /** Whether a session is believed to be open (the server has the last word). */
  isAuthenticated: () => boolean
}

const signedOut = {
  expiresAt: null,
  user: null,
  memberships: [],
  meLoaded: false,
  loadingMe: false,
  profileError: null,
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => {
      const begin = async (response: LoginResponse) => {
        setCsrfToken(response.csrf_token ?? null)
        set(state => ({
          expiresAt: Date.now() + response.session_expires_in * 1000,
          sessionEpoch: state.sessionEpoch + 1,
          meLoaded: false,
          profileError: null,
        }))
        await get().loadMe()
      }

      return {
        ...signedOut,
        sessionEpoch: 0,

        login: async (email, password) => {
          queryClient.clear()
          await begin(await api.login(email, password))
        },

        loginWithSso: async code => {
          queryClient.clear()
          await begin(await api.ssoExchange(code))
        },

        loadMe: async () => {
          const epoch = get().sessionEpoch
          set({ loadingMe: true, profileError: null })
          try {
            const profile: AuthProfile = await api.me()
            if (get().sessionEpoch !== epoch) return
            const { user_id, email, full_name, support_admin, memberships } = profile
            set({
              user: { user_id, email, full_name, support_admin: Boolean(support_admin) },
              memberships,
              meLoaded: true,
            })
          } catch (error) {
            if (get().sessionEpoch !== epoch) return
            set({ profileError: error instanceof Error ? error.message : String(error) })
            throw error
          } finally {
            if (get().sessionEpoch === epoch) set({ loadingMe: false })
          }
        },

        logout: () => {
          if (get().expiresAt !== null) void api.logout().catch(() => {})
          forgetSession()
        },

        isAuthenticated: () => {
          const { expiresAt } = get()
          return Boolean(expiresAt && expiresAt > Date.now())
        },
      }
    },
    {
      name: 'fabrique-auth-storage',
      // Never a credential: the access and refresh tokens are HttpOnly cookies.
      partialize: ({ expiresAt, user, memberships }) => ({ expiresAt, user, memberships }),
    },
  ),
)

/** Forget the session here; the server already ended it, or is being told to. */
function forgetSession() {
  queryClient.clear()
  setCsrfToken(null)
  useAuthStore.setState(state => ({ ...signedOut, sessionEpoch: state.sessionEpoch + 1 }))
  useAppStore.getState().setCompany(null, null)
  useAppStore.getState().setActiveSprint(null)
}

// The client asks the store which session a request belongs to, and tells it
// when the server refused to refresh that session.
setSessionHooks({
  current: () => (useAuthStore.getState().isAuthenticated() ? useAuthStore.getState().sessionEpoch : null),
  expired: epoch => {
    const state = useAuthStore.getState()
    if (state.sessionEpoch !== epoch || state.expiresAt === null) return false
    forgetSession()
    return true
  },
})
