/** The task page that shows a backlog item and the deliverables agents attached to it. */
export function taskPath(sprintId: string, taskId: number) {
  return `/sprints/${sprintId}/tasks/${taskId}`
}

// ── Agent identity ────────────────────────────────────────────────────────────

export interface AgentIdentity {
  /** The catalogue role, or the party, this identity stands for: one per agent. */
  key: string
  initials: string
  bg: string
  ring: string
  label: string
  color: string
  /** A role the company hires (an agent), not the system, a gate or a person. */
  isAgent: boolean
}

type Look = Omit<AgentIdentity, 'key' | 'isAgent'>

/**
 * The company's roles, by their catalogue name (`services/contracts/roles.py`).
 * Every stored spelling resolves to one of these through `ALIASES`.
 */
const ROLES: Record<string, Look> = {
  PO:                { initials: 'PO', bg: 'bg-purple-600',  ring: 'ring-purple-400/30',  label: 'Product Owner',  color: 'text-purple-300'  },
  Architect:         { initials: 'AR', bg: 'bg-indigo-600',  ring: 'ring-indigo-400/30',  label: 'Architect',      color: 'text-indigo-300'  },
  ScrumMaster:       { initials: 'SM', bg: 'bg-blue-600',    ring: 'ring-blue-400/30',    label: 'Scrum Master',   color: 'text-blue-300'    },
  Developer:         { initials: 'DV', bg: 'bg-green-600',   ring: 'ring-green-400/30',   label: 'Developer',      color: 'text-green-300'   },
  BackendDeveloper:  { initials: 'BE', bg: 'bg-cyan-600',    ring: 'ring-cyan-400/30',    label: 'Backend Dev',    color: 'text-cyan-300'    },
  FrontendDeveloper: { initials: 'FE', bg: 'bg-yellow-600',  ring: 'ring-yellow-400/30',  label: 'Frontend Dev',   color: 'text-yellow-300'  },
  QA:                { initials: 'QA', bg: 'bg-pink-600',    ring: 'ring-pink-400/30',    label: 'QA Engineer',    color: 'text-pink-300'    },
  DevOps:            { initials: 'DO', bg: 'bg-orange-600',  ring: 'ring-orange-400/30',  label: 'DevOps',         color: 'text-orange-300'  },
  DataEngineer:      { initials: 'DA', bg: 'bg-teal-600',    ring: 'ring-teal-400/30',    label: 'Data Engineer',  color: 'text-teal-300'    },
  SecOps:            { initials: 'SO', bg: 'bg-rose-600',    ring: 'ring-rose-400/30',    label: 'SecOps',         color: 'text-rose-300'    },
  TechWriter:        { initials: 'TW', bg: 'bg-lime-700',    ring: 'ring-lime-400/30',    label: 'Tech Writer',    color: 'text-lime-300'    },
  Designer:          { initials: 'DS', bg: 'bg-fuchsia-600', ring: 'ring-fuchsia-400/30', label: 'Designer',       color: 'text-fuchsia-300' },
}

/** The stored spellings that are not catalogue names (`roles.ALIASES`), by their normalised form. */
const ALIASES: Record<string, string> = {
  dev: 'Developer', engineer: 'Developer', softwareengineer: 'Developer', fullstackdev: 'Developer',
  mobiledev: 'Developer', mlengineer: 'Developer', backend: 'BackendDeveloper', backenddev: 'BackendDeveloper',
  frontend: 'FrontendDeveloper', frontenddev: 'FrontendDeveloper', data: 'DataEngineer', sre: 'DevOps',
  uxuidesigner: 'Designer', productowner: 'PO', sm: 'ScrumMaster', security: 'SecOps', tester: 'QA',
  quality: 'QA', qualityassurance: 'QA',
}

/**
 * The parties that write in a sprint without being agents: the system, the
 * Verifier and the quality gate (the platform's checks), and a person.
 */
const PARTIES: Record<string, Look> = {
  System:   { initials: '⚙',  bg: 'bg-gray-600',  ring: 'ring-gray-400/30',  label: 'System',       color: 'text-gray-400'  },
  Verifier: { initials: 'VF', bg: 'bg-slate-600', ring: 'ring-slate-400/30', label: 'Verifier',     color: 'text-slate-300' },
  Gate:     { initials: 'QG', bg: 'bg-slate-600', ring: 'ring-slate-400/30', label: 'Quality gate', color: 'text-slate-300' },
  Person:   { initials: 'P',  bg: 'bg-sky-700',   ring: 'ring-sky-400/30',   label: 'Person',       color: 'text-sky-300'   },
}
const PARTY_ALIASES: Record<string, string> = {
  system: 'System', verifier: 'Verifier', qualitygate: 'Gate', gate: 'Gate', deliverygate: 'Gate',
  user: 'Person', person: 'Person',
}

function normalise(key: string): string {
  return key.toLowerCase().replace(/[\s\-_/]/g, '')
}

/** The catalogue role or party *key* stands for, or null for a name the factory does not know. */
export function canonicalRole(key: string | undefined | null): string | null {
  if (!key) return null
  const n = normalise(key)
  const role = Object.keys(ROLES).find(name => normalise(name) === n) ?? ALIASES[n]
  if (role) return role
  return Object.keys(PARTIES).find(name => normalise(name) === n) ?? PARTY_ALIASES[n] ?? null
}

/** Two letters for a name the factory does not know: its capitals, else its first two letters. */
function initialsOf(name: string): string {
  const capitals = name.replace(/[^A-Z]/g, '')
  return (capitals.length >= 2 ? capitals : name).slice(0, 2).toUpperCase()
}

/**
 * The identity of the author *key* (a role, a stored alias or a party). A name
 * the factory does not know is shown as itself, never as a generic "Agent".
 */
export function getAgent(key: string): AgentIdentity {
  const canonical = canonicalRole(key)
  if (canonical && ROLES[canonical]) return { key: canonical, isAgent: true, ...ROLES[canonical] }
  if (canonical && PARTIES[canonical]) return { key: canonical, isAgent: false, ...PARTIES[canonical] }
  const name = (key || '').trim() || 'System'
  return {
    key: name, isAgent: true, initials: initialsOf(name), bg: 'bg-brand-600', ring: 'ring-brand-400/30',
    label: name, color: 'text-brand-300',
  }
}

/** The author of a message: its role when it names one (the agent's own name is not a role). */
export function authorKey(msg: { agent?: string; role?: string }): string {
  return msg.role || msg.agent || 'System'
}

export const FRESH_ACTIVITY_MS = 12_000
