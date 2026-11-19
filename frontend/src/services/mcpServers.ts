/**
 * The MCP servers a company's agents may call tools on (`routers/mcp.py`).
 * Every member reads the list (secrets by name only: the backend never
 * returns a value); a Manager adds servers from an `mcpServers` map, enables,
 * disables, scopes or removes one, and lists a server's tools, which starts
 * it (audited by the backend). What may be saved or started at all is the
 * operator's policy, which the backend reports as `start_refused`.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, fetchApi } from './api'

export interface McpServer {
  id: number
  company_id: number
  name: string
  transport: string
  url: string | null
  command: string | null
  args: string[]
  cwd: string | null
  /** Empty: every role may call it. */
  roles: string[]
  enabled: boolean
  description: string | null
  timeout_seconds: number | null
  created_by: string | null
  created_at: string | null
  updated_at: string | null
  /** Why this deployment would not start it now; none when it would. */
  start_refused: string | null
  env_keys: string[]
  header_names: string[]
  secrets_readable: boolean
}

export interface McpTool {
  name: string
  description?: string | null
}

export interface McpServerPatch {
  enabled?: boolean
  roles?: string[]
  description?: string
}

export const mcpApi = {
  list: (companyId: number) => api.get<{ servers: McpServer[] }>('/v1/mcp/servers', { company_id: companyId }),
  add: (companyId: number, mcpServers: Record<string, unknown>) =>
    api.post<{ servers: McpServer[] }>('/v1/mcp/servers', { company_id: companyId, mcpServers }),
  patch: (serverId: number, patch: McpServerPatch) => api.patch<McpServer>(`/v1/mcp/servers/${serverId}`, patch),
  remove: (serverId: number) => api.delete<void>(`/v1/mcp/servers/${serverId}`),
  /** Starts the server (audited); never resent. */
  tools: (serverId: number) =>
    fetchApi<{ server: string; tools: McpTool[] }>(`/v1/mcp/servers/${serverId}/tools`, { once: true, timeoutMs: 45_000 }),
}

/**
 * The pasted definition as the `mcpServers` map, or the reason it is not one.
 * Accepts `{"mcpServers": {...}}` or the bare map, as the backend does.
 */
export function parseServersMap(text: string): { map: Record<string, unknown> } | { error: 'json' | 'shape' | 'empty' } {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { error: 'json' }
  }
  const map = parsed && typeof parsed === 'object' && !Array.isArray(parsed) && 'mcpServers' in parsed
    ? (parsed as { mcpServers: unknown }).mcpServers
    : parsed
  if (!map || typeof map !== 'object' || Array.isArray(map)) return { error: 'shape' }
  if (Object.keys(map).length === 0) return { error: 'empty' }
  return { map: map as Record<string, unknown> }
}

const serversKey = (companyId: number | null) => ['mcp-servers', companyId] as const

export function useMcpServers(companyId: number | null) {
  return useQuery({ queryKey: serversKey(companyId), queryFn: () => mcpApi.list(companyId as number), enabled: companyId !== null })
}

type McpChange =
  | { kind: 'add'; map: Record<string, unknown> }
  | { kind: 'patch'; serverId: number; patch: McpServerPatch }
  | { kind: 'remove'; serverId: number }

export function useMcpChange(companyId: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (change: McpChange): Promise<unknown> => {
      switch (change.kind) {
        case 'add': return mcpApi.add(companyId as number, change.map)
        case 'patch': return mcpApi.patch(change.serverId, change.patch)
        case 'remove': return mcpApi.remove(change.serverId)
      }
    },
    onSuccess: () => client.invalidateQueries({ queryKey: serversKey(companyId) }),
  })
}

export function useMcpTools() {
  return useMutation({ mutationFn: (serverId: number) => mcpApi.tools(serverId) })
}
