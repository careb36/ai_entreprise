import { fetchApi, type Http } from './client'

export interface Capabilities {
  agent_graph: boolean
  memory: boolean
  memory_graph?: boolean // the knowledge graph is recorded (NEO4J_ENABLED), apart from pgvector memory
  github: boolean
  auth_required: boolean
  /** The Support agent triages new tickets (SUPPORT_AGENT_ENABLED). */
  support_agent?: boolean
  /** Where GitHub can reach the backend (PUBLIC_API_URL); null when unset. */
  public_api_url?: string | null
}

export type ComponentStatus = { status: 'up' | 'down' | 'disabled'; reason?: string; detail?: string }

export interface DeepHealth {
  status: 'healthy' | 'degraded' | 'down'
  components: Record<string, ComponentStatus>
  nats?: string
}

export const systemEndpoints = (api: Http) => ({
  // Core & Enterprise
  health: () => api.get<{ status: string; timestamp: string }>('/health'),
  // Probes each backing service; slow when one is down, so only on demand.
  deepHealth: () => fetchApi<DeepHealth>('/health/deep', { method: 'GET', auth: false, retryServerErrors: false }),
  capabilities: () => fetchApi<Capabilities>('/v1/capabilities', { method: 'GET', auth: false }),
})
