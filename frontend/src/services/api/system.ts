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
  /** The widest forge policy a company may choose here (FORGE_POLICY_MAX, issue #745). */
  forge_policy_max?: 'none' | 'fetch' | 'publish'
}

export type ComponentStatus = { status: 'up' | 'down' | 'disabled'; reason?: string; detail?: string }

/** One capability of the deployment (issue #751): on, off or degraded, and why; never a setting's value. */
export interface CapabilityState {
  state: 'on' | 'off' | 'degraded'
  reason: string
  provider?: string
  model?: string
  scope?: string
  providers?: string[]
}

export interface DeepHealth {
  status: 'healthy' | 'degraded' | 'down'
  components: Record<string, ComponentStatus>
  nats?: string
  /** The backend's capabilities and the LLM proxy's (`llm.*`); never changes `status`. */
  capabilities?: Record<string, CapabilityState>
}

export const systemEndpoints = (api: Http) => ({
  // Core & Enterprise
  health: () => api.get<{ status: string; timestamp: string }>('/health'),
  // Probes each backing service; slow when one is down, so only on demand.
  deepHealth: () => fetchApi<DeepHealth>('/health/deep', { method: 'GET', auth: false, retryServerErrors: false }),
  capabilities: () => fetchApi<Capabilities>('/v1/capabilities', { method: 'GET', auth: false }),
})
