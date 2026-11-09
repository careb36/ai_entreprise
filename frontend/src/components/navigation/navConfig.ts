import {
  LayoutDashboard,
  ClipboardCheck,
  ShieldCheck,
  Code2,
  Database,
  type LucideIcon,
} from 'lucide-react'

export interface NavChildItem {
  id: string
  label: string
  path: string
  description?: string
  badge?: string | number
  isExternal?: boolean
}

export interface NavGroup {
  id: string
  label: string
  path: string
  icon: LucideIcon
  description: string
  badgeKey?: 'waiting' | 'activeTasks'
  capability?: string
  children: NavChildItem[]
}

/**
 * Hick's Law: Decision time increases logarithmically with the number of options: T = b * log2(n + 1).
 * Grouping related items into 5 top-level categories minimizes decision fatigue.
 */
export const HICKS_LAW_NAV_LIMIT = 5

export const NAV_ITEMS: NavGroup[] = [
  {
    id: 'sprints',
    label: 'Sprints & Work',
    path: '/sprints',
    icon: LayoutDashboard,
    description: 'Sprint planning, backlog & agent execution',
    badgeKey: 'activeTasks',
    children: [
      { id: 'all-sprints', label: 'All Sprints', path: '/sprints', description: 'Sprint boards and velocity' },
      { id: 'active-tasks', label: 'Task Execution', path: '/sprints?view=tasks', description: 'Agent task lifecycle' },
      { id: 'backlog', label: 'Backlog Planning', path: '/sprints?view=backlog', description: 'Task breakdown & readiness' },
    ],
  },
  {
    id: 'evidence',
    label: 'Evidence & Runs',
    path: '/evidence',
    icon: ClipboardCheck,
    description: 'Attestations, verification logs & artifacts',
    children: [
      { id: 'evidence-vault', label: 'Evidence Vault', path: '/evidence', description: 'Cryptographic execution evidence' },
      { id: 'verifications', label: 'Verification Runs', path: '/evidence?tab=verifications', description: 'Automated test & AST check results' },
      { id: 'attestations', label: 'Attestations', path: '/evidence?tab=attestations', description: 'Signed security attestations' },
    ],
  },
  {
    id: 'governance',
    label: 'Governance & Review',
    path: '/governance',
    icon: ShieldCheck,
    description: 'Human-in-the-loop approvals & audit trail',
    badgeKey: 'waiting',
    children: [
      { id: 'review-inbox', label: 'Review Inbox', path: '/governance', description: 'Items requiring human review' },
      { id: 'governance-rules', label: 'Policies & Gates', path: '/governance?tab=rules', description: 'Autonomous safety gates' },
      { id: 'audit-log', label: 'Audit Trail', path: '/governance?tab=audit', description: 'Tamper-evident activity logs' },
    ],
  },
  {
    id: 'generate',
    label: 'Agent Studio',
    path: '/generate',
    icon: Code2,
    description: 'Autonomous agent graph & code generation',
    capability: 'agent_graph',
    children: [
      { id: 'agent-orchestrator', label: 'Graph Orchestrator', path: '/generate', description: 'LangGraph multi-agent pipelines' },
      { id: 'code-gen', label: 'Code Synthesis', path: '/generate?tab=code', description: 'AST validation and code emission' },
      { id: 'sandbox-runs', label: 'Sandbox Execution', path: '/generate?tab=sandbox', description: 'Ephemeral container execution' },
    ],
  },
  {
    id: 'memory',
    label: 'Knowledge & Memory',
    path: '/memory',
    icon: Database,
    description: 'Episodic memory, Neo4j graph & pgvector index',
    capability: 'memory',
    children: [
      { id: 'episodic-memory', label: 'Episodic Memory', path: '/memory', description: 'Sprint retrospectives & decisions' },
      { id: 'graph-explorer', label: 'Graph Relationships', path: '/memory?tab=graph', description: 'Neo4j knowledge graph explorer' },
      { id: 'vector-search', label: 'Vector Similarity', path: '/memory?tab=vectors', description: 'pgvector semantic context retrieval' },
    ],
  },
]

export function isNavGroupActive(group: NavGroup, currentPath: string): boolean {
  if (currentPath === group.path) return true
  if (currentPath.startsWith(`${group.path}/`) || currentPath.startsWith(`${group.path}?`)) return true
  return group.children.some(child => currentPath === child.path || currentPath.startsWith(`${child.path}/`))
}

export function filterNavByCapabilities(
  items: NavGroup[] = NAV_ITEMS,
  capabilities?: { agent_graph?: boolean; memory?: boolean } | null
): NavGroup[] {
  return items.filter(item => {
    if (!item.capability) return true
    if (!capabilities) return true
    if (item.capability === 'agent_graph') return capabilities.agent_graph !== false
    if (item.capability === 'memory') return capabilities.memory !== false
    return true
  }).slice(0, HICKS_LAW_NAV_LIMIT)
}
