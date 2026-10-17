import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { api, type AuthProfile, type AuthUser, type CompanyMembership } from '../services/api'
import { useAppStore } from './useAppStore'

interface AuthState {
  token: string | null
  expiresAt: number | null
  user: AuthUser | null
  memberships: CompanyMembership[]
  meLoaded: boolean
  loadingMe: boolean
  profileError: string | null
  login: (email: string, password: string) => Promise<void>
  loadMe: () => Promise<void>
  logout: () => void
  getValidToken: () => string | null
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      expiresAt: null,
      user: null,
      memberships: [],
      meLoaded: false,
      loadingMe: false,
      profileError: null,

      login: async (email, password) => {
        const response = await api.login(email, password)
        set({
          token: response.access_token,
          expiresAt: Date.now() + response.expires_in * 1000,
          meLoaded: false,
          profileError: null,
        })
        await get().loadMe()
      },

      loadMe: async () => {
        set({ loadingMe: true, profileError: null })
        try {
          const profile: AuthProfile = await api.me()
          const { user_id, email, full_name, memberships } = profile
          set({
            user: { user_id, email, full_name },
            memberships,
            meLoaded: true,
          })
        } catch (error) {
          set({ profileError: error instanceof Error ? error.message : String(error) })
          throw error
        } finally {
          set({ loadingMe: false })
        }
      },

      logout: () => {
        set({
          token: null,
          expiresAt: null,
          user: null,
          memberships: [],
          meLoaded: false,
          loadingMe: false,
          profileError: null,
        })
        useAppStore.getState().setCompany(null, null)
        useAppStore.getState().setActiveSprint(null)
      },

      getValidToken: () => {
        const { token, expiresAt } = get()
        if (!token || !expiresAt || expiresAt <= Date.now()) {
          if (token || expiresAt) get().logout()
          return null
        }
        return token
      },
    }),
    {
      name: 'fabrique-auth-storage',
      partialize: ({ token, expiresAt, user, memberships }) => ({
        token,
        expiresAt,
        user,
        memberships,
      }),
    },
  ),
)
