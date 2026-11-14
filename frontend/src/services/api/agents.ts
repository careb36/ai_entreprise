import type { Http } from './client'

export interface AgentActivity {
  total_tasks: number
  last_run_at: string | null
  total_cost_usd: number | null
  total_tokens: number
  avg_tokens: number
  last_model: string | null
}

export interface ManagedAgent {
  id: number
  company_id: number
  name: string
  role: string
  persona?: string | null
  seniority: string
  specialty: string
  model: string
  system_prompt?: string | null
  token_budget: number
  activity?: AgentActivity
}

export interface ModelCatalogEntry {
  id: string
  provider: string
  tier: string
  description: string
}

export const agentEndpoints = (api: Http) => ({
  // ── Agents ────────────────────────────────────────────────────────────────
  getAgents: (companyId: number) =>
    api.get<{ agents: ManagedAgent[] }>('/v1/agents', { company_id: companyId }),

  createAgent: (data: {
    company_id: number
    name: string
    role: string
    persona?: string
    seniority: string
    specialty: string
    model: string
    system_prompt?: string
    token_budget: number
  }) => api.post<ManagedAgent>('/v1/agents', data),

  generateAgentPrompt: (data: { role: string; seniority: string; specialty?: string; persona?: string }) =>
    api.post<{ system_prompt: string }>('/v1/agents/generate_prompt', data),

  updateAgent: (agentId: number, data: Partial<{
    name: string; role: string; persona: string; seniority: string
    specialty: string; model: string; system_prompt: string; token_budget: number
  }>) => api.patch<ManagedAgent>(`/v1/agents/${agentId}`, data),

  deleteAgent: (agentId: number) =>
    api.delete<{ status: string; agent: ManagedAgent }>(`/v1/agents/${agentId}`),
})
