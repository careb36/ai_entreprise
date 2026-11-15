import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type ChatConnectionInput, type ChatProvider } from '../services/api'

const settingsKey = (companyId: number | null) => ['chat', 'settings', companyId] as const
const linksKey = (companyId: number | null) => ['chat', 'links', companyId] as const

/** The company's Slack and Teams connections, for its Owners. */
export function useChatSettings(companyId: number | null, enabled: boolean) {
  return useQuery({
    queryKey: settingsKey(companyId),
    queryFn: () => api.getChat(companyId as number),
    enabled: enabled && companyId !== null,
  })
}

export function useSaveChat(companyId: number | null, provider: ChatProvider) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (data: ChatConnectionInput) => api.saveChat(companyId as number, provider, data),
    onSuccess: () => client.invalidateQueries({ queryKey: ['chat'] }),
  })
}

export function useTestChat(companyId: number | null, provider: ChatProvider) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.testChat(companyId as number, provider),
    onSuccess: () => client.invalidateQueries({ queryKey: settingsKey(companyId) }),
  })
}

export function useDeleteChat(companyId: number | null, provider: ChatProvider) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.deleteChat(companyId as number, provider),
    onSuccess: () => client.invalidateQueries({ queryKey: ['chat'] }),
  })
}

/** The signed-in person's own chat links in the company. */
export function useChatLinks(companyId: number | null) {
  return useQuery({
    queryKey: linksKey(companyId),
    queryFn: () => api.getChatLinks(companyId as number),
    enabled: companyId !== null,
  })
}

export function useChatLinkCode(companyId: number | null) {
  return useMutation({
    mutationFn: (provider: ChatProvider) => api.chatLinkCode(companyId as number, provider),
  })
}

export function useUnlinkChat(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (provider: ChatProvider) => api.unlinkChat(companyId as number, provider),
    onSuccess: () => client.invalidateQueries({ queryKey: linksKey(companyId) }),
  })
}
