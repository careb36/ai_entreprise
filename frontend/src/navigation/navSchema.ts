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
  badgeKey?: 'inboxCount'
  capability?: 'agent_graph' | 'memory'
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
 * Global Navigation Schema adhering to Hick's Law.
 * Reduces top-level choices to exactly 5 hierarchical categories to minimize decision fatigue:
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
        id: 'evidence-records',
        labelKey: 'nav.items.evidence',
        defaultLabel: 'Evidence Records',
        to: '/evidence',
        icon: ClipboardCheck,
      },
      {
        id: 'governance-review',
        labelKey: 'nav.items.governance',
        defaultLabel: 'Governance Inbox',
        to: '/governance',
        icon: CheckSquare,
        badgeKey: 'inboxCount',
      },
    ],
  },
  {
    id: 'settings',
    labelKey: 'nav.categories.settings',
    defaultLabel: 'Settings',
    icon: Settings,
    defaultPath: '/company',
    items: [
      {
        id: 'organization',
        labelKey: 'nav.items.company',
        defaultLabel: 'Organization',
        to: '/company',
        icon: Building2,
      },
      {
        id: 'agent-config',
        labelKey: 'nav.items.agents',
        defaultLabel: 'Agents & Policies',
        to: '/agents',
        icon: Bot,
      },
      {
        id: 'support-admin',
        labelKey: 'nav.items.support',
        defaultLabel: 'Support & Diagnostics',
        to: '/support',
        icon: LifeBuoy,
      },
    ],
  },
] as const

export function isPathActive(pathname: string, targetPath: string, exact = false): boolean {
  if (exact) {
    return pathname === targetPath
  }
  return pathname === targetPath || pathname.startsWith(`${targetPath}/`)
}

export function findActiveCategory(
  pathname: string,
  categories: readonly NavRootCategory[] = NAVIGATION_CATEGORIES
): NavRootCategory['id'] | null {
  for (const cat of categories) {
    if (isPathActive(pathname, cat.defaultPath)) {
      return cat.id
    }
    for (const item of cat.items) {
      if (isPathActive(pathname, item.to, item.exact)) {
        return cat.id
      }
    }
  }
  return null
}
