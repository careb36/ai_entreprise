import { useState } from 'react'
import type { TFunction } from 'i18next'
import type { Agent, AgentDraft } from './types'

/**
 * Step 4's state: the team being drafted. A default agent keeps a translated
 * specialty until the person edits it.
 */
export function useTeamDrafts(t: TFunction) {
  const [agentDrafts, setAgents] = useState<AgentDraft[]>([
    { name: '', role: 'PO', persona: '', seniority: 'Senior', specialty: '', defaultSpecialtyKey: 'productSpecialty', token_budget: 1000000 },
    { name: '', role: 'Architect', persona: '', seniority: 'Senior', specialty: '', token_budget: 2000000 },
    { name: '', role: 'Dev', persona: '', seniority: 'Mid', specialty: '', token_budget: 1000000 },
    { name: '', role: 'QA', persona: '', seniority: 'Senior', specialty: '', defaultSpecialtyKey: 'qaSpecialty', token_budget: 500000 },
  ])
  const agents: Agent[] = agentDrafts.map(({ defaultSpecialtyKey, ...agent }) => ({
    ...agent,
    specialty: defaultSpecialtyKey ? t(`companyNew.defaults.${defaultSpecialtyKey}`) : agent.specialty,
  }))

  const addAgent = () => setAgents(prev => [...prev, {
    name: '', role: 'Dev', persona: '', seniority: 'Mid', specialty: '', token_budget: 1000000,
  }])

  const removeAgent = (i: number) => setAgents(prev => prev.filter((_, idx) => idx !== i))

  const updateAgent = (i: number, field: keyof Agent, val: string | number) =>
    setAgents(prev => prev.map((a, idx) => idx === i ? {
      ...a,
      [field]: val,
      defaultSpecialtyKey: field === 'specialty' || field === 'persona' ? undefined : a.defaultSpecialtyKey,
    } : a))

  return { agents, setAgents, addAgent, removeAgent, updateAgent }
}
