import type { ComponentType } from 'react'
import {
  LayoutDashboard,
  Network,
  Cpu,
  ShieldCheck,
  Settings,
  ClipboardCheck,
  CheckSquare,
  GitBranch,
  Database,
  Building2,
  Bot,
  LifeBuoy,
} from 'lucide-react'

export interface NavSubItem {
  id: string
  labelKey: string
  defaultLabel: string
  to: string
  icon: ComponentType<{ size?: number; className?: string }>
  badgeKey?: 'inboxCount' | 'alertCount'
  capability?: 'agent_graph' | 'memory' | 'audit'
  exact?: boolean
}

export interface NavRootCategory {
  id: 'dashboard' | 'graph_topology' | 'orchestration' | 'audit' | 'settings'
  labelKey: string
  defaultLabel: string
  icon: ComponentType<{ size?: number; className?: string }>
  defaultPath: string
  items: NavSubItem[]
}

/**
 * Global Navigation Schema adhering strictly to Hick's Law.
 * Top-level choices are capped at exactly 5 categories to minimize decision fatigue:
 * 1. Dashboard (Workspaces & Sprints)
 * 2. Graph Topology (Agent Artifact Graphs & DAGs)
 * 3. Orchestration (Memory, RAG & Autonomous Execution)
 * 4. Audit & Evidence (Verification Evidence & Governance Review)
 * 5. Settings & Governance (Company, Agents, Support)
 */
export const NAVIGATION_CATEGORIES: readonly NavRootCategory[] = [
  {
    id: 'dashboard',
    labelKey: 'nav.categories.dashboard',
    defaultLabel: 'Dashboard',
    icon: LayoutDashboard,
    defaultPath: '/sprints',
    items: [
      {
        id: 'sprints-overview',
        labelKey: 'nav.items.sprints',
        defaultLabel: 'Sprints & Tasks',
        to: '/sprints',
        icon: LayoutDashboard,
      },
      {
        id: 'tasks-board',
        labelKey: 'nav.items.tasks',
        defaultLabel: 'Sprint Board',
        to: '/board',
        icon: CheckSquare,
      },
    ],
  },
  {
    id: 'graph_topology',
    labelKey: 'nav.categories.graph_topology',
    defaultLabel: 'Graph Topology',
    icon: Network,
    defaultPath: '/generate',
    items: [
      {
        id: 'agent-graph',
        labelKey: 'nav.items.generate',
        defaultLabel: 'Agent Graph',
        to: '/generate',
        icon: GitBranch,
        capability: 'agent_graph',
      },
    ],
  },
  {
    id: 'orchestration',
    labelKey: 'nav.categories.orchestration',
    defaultLabel: 'Orchestration',
    icon: Cpu,
    defaultPath: '/memory',
    items: [
      {
        id: 'memory-store',
        labelKey: 'nav.items.memory',
        defaultLabel: 'Memory & Context',
        to: '/memory',
        icon: Database,
        capability: 'memory',
      },
    ],
  },
  {
    id: 'audit',
    labelKey: 'nav.categories.audit',
    defaultLabel: 'Audit & Evidence',
    icon: ShieldCheck,
    defaultPath: '/evidence',
    items: [
      {
        id: 'evidence-log',
        labelKey: 'nav.items.evidence',
        defaultLabel: 'Verification Evidence',
        to: '/evidence',
        icon: ShieldCheck,
        badgeKey: 'alertCount',
      },
      {
        id: 'audit-trail',
        labelKey: 'nav.items.audit',
        defaultLabel: 'Audit Logs',
        to: '/audit',
        icon: ClipboardCheck,
      },
    ],
  },
  {
    id: 'settings',
    labelKey: 'nav.categories.settings',
    defaultLabel: 'Settings & Governance',
    icon: Settings,
    defaultPath: '/company',
    items: [
      {
        id: 'company-settings',
        labelKey: 'nav.items.company',
        defaultLabel: 'Company Profile',
        to: '/company',
        icon: Building2,
      },
      {
        id: 'agents-config',
        labelKey: 'nav.items.agents',
        defaultLabel: 'Configured Agents',
        to: '/agents',
        icon: Bot,
      },
      {
        id: 'support-docs',
        labelKey: 'nav.items.support',
        defaultLabel: 'Support & Help',
        to: '/support',
        icon: LifeBuoy,
      },
    ],
  },
] as const

/**
 * Returns the active root category based on current pathname.
 */
export function getActiveCategory(
  pathname: string,
  categories: readonly NavRootCategory[] = NAVIGATION_CATEGORIES
): NavRootCategory | undefined {
  return categories.find((cat) =>
    cat.items.some((sub) => (sub.exact ? pathname === sub.to : pathname.startsWith(sub.to)))
  )
}

/**
 * Finds a subitem by matching its route to the pathname.
 */
export function findSubItemByPath(
  pathname: string,
  categories: readonly NavRootCategory[] = NAVIGATION_CATEGORIES
): NavSubItem | undefined {
  for (const cat of categories) {
    const match = cat.items.find((sub) =>
      sub.exact ? pathname === sub.to : pathname.startsWith(sub.to)
    )
    if (match) return match
  }
  return undefined
}
