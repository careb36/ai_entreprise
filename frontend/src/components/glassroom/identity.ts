/** The task page that shows a backlog item and the deliverables agents attached to it. */
export function taskPath(sprintId: string, taskId: number) {
  return `/sprints/${sprintId}/tasks/${taskId}`
}

// ── Agent identity ────────────────────────────────────────────────────────────

export interface AgentIdentity {
  initials: string
  bg: string
  ring: string
  label: string
  color: string
}

const AGENTS: Record<string, AgentIdentity> = {
  PO:           { initials: 'PO', bg: 'bg-purple-600',  ring: 'ring-purple-400/30', label: 'Product Owner',  color: 'text-purple-300'  },
  Architect:    { initials: 'AR', bg: 'bg-indigo-600',  ring: 'ring-indigo-400/30', label: 'Architect',      color: 'text-indigo-300'  },
  ScrumMaster:  { initials: 'SM', bg: 'bg-blue-600',    ring: 'ring-blue-400/30',   label: 'Scrum Master',   color: 'text-blue-300'    },
  Developer:    { initials: 'DV', bg: 'bg-green-600',   ring: 'ring-green-400/30',  label: 'Developer',      color: 'text-green-300'   },
  Dev:          { initials: 'DV', bg: 'bg-green-600',   ring: 'ring-green-400/30',  label: 'Developer',      color: 'text-green-300'   },
  Backend:      { initials: 'BE', bg: 'bg-cyan-600',    ring: 'ring-cyan-400/30',   label: 'Backend Dev',    color: 'text-cyan-300'    },
  Frontend:     { initials: 'FE', bg: 'bg-yellow-600',  ring: 'ring-yellow-400/30', label: 'Frontend Dev',   color: 'text-yellow-300'  },
  QA:           { initials: 'QA', bg: 'bg-pink-600',    ring: 'ring-pink-400/30',   label: 'QA Engineer',    color: 'text-pink-300'    },
  DevOps:       { initials: 'DO', bg: 'bg-orange-600',  ring: 'ring-orange-400/30', label: 'DevOps',         color: 'text-orange-300'  },
  Data:         { initials: 'DA', bg: 'bg-teal-600',    ring: 'ring-teal-400/30',   label: 'Data Engineer',  color: 'text-teal-300'    },
  DataEngineer: { initials: 'DA', bg: 'bg-teal-600',    ring: 'ring-teal-400/30',   label: 'Data Engineer',  color: 'text-teal-300'    },
  System:       { initials: '⚙',  bg: 'bg-gray-600',    ring: 'ring-gray-400/30',   label: 'System',         color: 'text-gray-400'    },
}

const FALLBACK_AGENT: AgentIdentity = {
  initials: 'AI', bg: 'bg-brand-600', ring: 'ring-brand-400/30', label: 'Agent', color: 'text-brand-300',
}

export const ACTIVE_AGENT_TTL_MS = 90_000
export const FRESH_ACTIVITY_MS = 12_000

export function getAgent(key: string): AgentIdentity {
  return (
    AGENTS[key] ??
    AGENTS[Object.keys(AGENTS).find(k => k.toLowerCase() === key?.toLowerCase()) ?? ''] ??
    FALLBACK_AGENT
  )
}
