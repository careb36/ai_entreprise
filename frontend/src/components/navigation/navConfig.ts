import {
  ShieldCheck,
  GitFork,
  Scale,
  Activity,
  ClipboardList,
  Bot,
  Sliders,
  CheckCircle2,
  FileSearch,
  Building,
  Terminal,
} from 'lucide-react'
import type { NavGroup } from './types'

/**
 * Global Navigation Configuration (Hick's Law Redesign)
 *
 * Hick's Law states that decision time increases logarithmically with the number
 * of options: T = b * log2(n + 1). By condensing previously sprawling top-level links
 * into exactly 5 logical, high-cohesion top-level groups, cognitive friction and
 * decision fatigue are significantly reduced while preserving instant access to all
 * deep views.
 *
 * The 5 Top-Level Groups:
 * 1. Evidence & Lineage (Evidence store, Artifact verification, Knowledge graph)
 * 2. Governance & Review (Human-in-the-loop inbox, Policy gates)
 * 3. Execution & Runs (Agent workflow executions, Sandboxed validation)
 * 4. Audit & Compliance (Immutable audit trail, Telemetry logs)
 * 5. Workspace & Settings (Agent fleet configuration, Quotas, Workspaces)
 */
export function getNavGroups(waitingReviewsCount: number = 0): NavGroup[] {
  return [
    {
      id: 'evidence',
      section: 'evidence',
      primaryTo: '/evidence',
      labelKey: 'nav.groups.evidence',
      defaultLabel: 'Evidence & Graph',
      icon: ShieldCheck,
      items: [
        {
          id: 'evidence-store',
          to: '/evidence',
          labelKey: 'nav.items.evidenceStore',
          defaultLabel: 'Evidence Records',
          description: 'Cryptographically signed agent artifacts and execution proofs',
          icon: FileSearch,
        },
        {
          id: 'knowledge-graph',
          to: '/graph',
          labelKey: 'nav.items.knowledgeGraph',
          defaultLabel: 'Knowledge Graph',
          description: 'Interactive artifact lineage and dependency visualization',
          icon: GitFork,
        },
      ],
    },
    {
      id: 'governance',
      section: 'governance',
      primaryTo: '/governance',
      labelKey: 'nav.groups.governance',
      defaultLabel: 'Governance',
      icon: Scale,
      badgeCount: waitingReviewsCount,
      items: [
        {
          id: 'review-inbox',
          to: '/governance',
          labelKey: 'nav.items.reviewInbox',
          defaultLabel: 'Review Inbox',
          description: 'Pending human approvals and gate verification requests',
          icon: CheckCircle2,
          badgeCount: waitingReviewsCount,
        },
        {
          id: 'policies',
          to: '/governance/policies',
          labelKey: 'nav.items.policies',
          defaultLabel: 'Security Policies',
          description: 'Active compliance gates and AST enforcement thresholds',
          icon: Scale,
        },
      ],
    },
    {
      id: 'executions',
      section: 'runs',
      primaryTo: '/runs',
      labelKey: 'nav.groups.executions',
      defaultLabel: 'Executions',
      icon: Activity,
      items: [
        {
          id: 'active-runs',
          to: '/runs',
          labelKey: 'nav.items.activeRuns',
          defaultLabel: 'Agent Runs',
          description: 'Active orchestrations, state machines, and task execution',
          icon: Activity,
        },
        {
          id: 'terminal',
          to: '/runs/console',
          labelKey: 'nav.items.console',
          defaultLabel: 'Execution Logs',
          description: 'Live daemon stdout/stderr and sandbox status',
          icon: Terminal,
        },
      ],
    },
    {
      id: 'audit',
      section: 'audit',
      primaryTo: '/audit',
      labelKey: 'nav.groups.audit',
      defaultLabel: 'Audit Trail',
      icon: ClipboardList,
      items: [
        {
          id: 'audit-log',
          to: '/audit',
          labelKey: 'nav.items.auditLog',
          defaultLabel: 'Audit Records',
          description: 'Non-repudiable system event ledger and access claims',
          icon: ClipboardList,
        },
        {
          id: 'telemetry',
          to: '/audit/telemetry',
          labelKey: 'nav.items.telemetry',
          defaultLabel: 'System Telemetry',
          description: 'Health metrics, latency counters, and token consumption',
          icon: Activity,
        },
      ],
    },
    {
      id: 'workspace',
      section: 'workspace',
      primaryTo: '/agents',
      labelKey: 'nav.groups.workspace',
      defaultLabel: 'Workspace',
      icon: Sliders,
      items: [
        {
          id: 'agents',
          to: '/agents',
          labelKey: 'nav.items.agents',
          defaultLabel: 'Agent Fleet',
          description: 'Configure autonomous workers, tools, and LLM providers',
          icon: Bot,
        },
        {
          id: 'quotas',
          to: '/company/quotas',
          labelKey: 'nav.items.quotas',
          defaultLabel: 'Quotas & Limits',
          description: 'Token limits, compute budgets, and rate limiting',
          icon: Sliders,
        },
        {
          id: 'companies',
          to: '/companies/select',
          labelKey: 'nav.items.selectCompany',
          defaultLabel: 'Switch Company',
          description: 'Manage tenant context and organizational units',
          icon: Building,
        },
      ],
    },
  ]
}

export const TOP_LEVEL_GROUPS_LIMIT = 5
