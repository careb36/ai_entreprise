import type { ManagedAgent } from '../../services/api'

export type AgentDraft = {
  name: string
  role: string
  persona: string
  seniority: string
  specialty: string
  model: string
  system_prompt: string
  token_budget: number
}

export function defaultDraft(): AgentDraft {
  return {
    name: '',
    role: 'BackendDeveloper',
    persona: '',
    seniority: 'Senior',
    specialty: '',
    // Empty means "Default for role": the backend resolves it from model_factory.
    model: '',
    system_prompt: '',
    token_budget: 500000,
  }
}

export function draftFromAgent(agent: ManagedAgent): AgentDraft {
  return {
    name: agent.name || '',
    role: agent.role || 'BackendDeveloper',
    persona: agent.persona || '',
    seniority: agent.seniority || 'Senior',
    specialty: agent.specialty || '',
    model: agent.model || '',
    system_prompt: agent.system_prompt || '',
    token_budget: agent.token_budget || 500000,
  }
}

export function isDraftDirty(draft: AgentDraft, agent: ManagedAgent): boolean {
  const original = draftFromAgent(agent)
  return (
    draft.name !== original.name ||
    draft.role !== original.role ||
    draft.persona !== original.persona ||
    draft.seniority !== original.seniority ||
    draft.specialty !== original.specialty ||
    draft.model !== original.model ||
    draft.system_prompt !== original.system_prompt ||
    draft.token_budget !== original.token_budget
  )
}
