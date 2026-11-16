import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { QUERY_KEYS } from '../../services/queryKeys'
import {
  api,
  type ManagedAgent,
} from '../../services/api'

// The managed agents, generation and the shared memory.
// Re-exported by hooks/useQueries.ts, which callers import.

export function useAgents(companyId: number | null) {
  return useQuery({
    queryKey: QUERY_KEYS.agents(companyId || 0),
    queryFn: () => api.getAgents(companyId!),
    enabled: !!companyId,
    refetchInterval: 30_000,
  })
}

export function useCreateAgent(companyId: number | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: Parameters<typeof api.createAgent>[0]) => api.createAgent(data),
    onSuccess: () => {
      if (companyId) {
        queryClient.invalidateQueries({ queryKey: QUERY_KEYS.agents(companyId) })
      }
    },
  })
}

export function useUpdateAgentConfig(companyId: number | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ agentId, data }: { agentId: number; data: Parameters<typeof api.updateAgent>[1] }) =>
      api.updateAgent(agentId, data),
    onSuccess: (updatedAgent: ManagedAgent) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.agents(updatedAgent.company_id || companyId || 0) })
    },
  })
}

export function useDeleteAgent(companyId: number | null) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (agentId: number) => api.deleteAgent(agentId),
    onSuccess: () => {
      if (companyId) {
        queryClient.invalidateQueries({ queryKey: QUERY_KEYS.agents(companyId) })
      }
    },
  })
}

export function useGenerateAgentPrompt() {
  return useMutation({
    mutationFn: (data: Parameters<typeof api.generateAgentPrompt>[0]) =>
      api.generateAgentPrompt(data),
  })
}

export function useGenerate() {
  return useMutation({
    mutationFn: api.generate,
  })
}

export function useSearchMemory(query: string, companyId: number | null, limit = 10) {
  return useQuery({
    queryKey: [...QUERY_KEYS.memory(companyId, query), limit],
    queryFn: () => api.searchMemory(query, { companyId, limit }),
    enabled: query.trim().length > 0,
  })
}

export function useStoreMemory() {
  return useMutation({
    mutationFn: ({ content, companyId, metadata }: { content: string; companyId: number | null; metadata?: Record<string, unknown> }) =>
      api.storeMemory(content, { companyId, metadata }),
  })
}
