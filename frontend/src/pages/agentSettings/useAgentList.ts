import { useMemo, useState } from 'react'
import type { ManagedAgent } from '../../services/api'
import { sortRoles } from './display'

/** The list's search, role filter, grouping by role, and which cards and groups are open. */
export function useAgentList(allAgents: ManagedAgent[]) {
  const [searchQuery, setSearchQuery] = useState('')
  const [activeRoleFilter, setActiveRoleFilter] = useState<string | null>(null)
  const [expandedAgents, setExpandedAgents] = useState<Set<number>>(() => new Set())
  const [collapsedRoles, setCollapsedRoles] = useState<Set<string>>(() => new Set())

  // Build per-role counts before applying search/filter so chips show totals.
  const roleCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const agent of allAgents) {
      const role = agent.role || 'Other'
      counts.set(role, (counts.get(role) ?? 0) + 1)
    }
    return counts
  }, [allAgents])

  // Apply role filter then text search.
  const visibleAgents = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    return allAgents.filter(agent => {
      if (activeRoleFilter && agent.role !== activeRoleFilter) return false
      if (!q) return true
      const haystack = [
        agent.name,
        agent.role,
        agent.specialty,
        agent.model,
        agent.seniority,
        agent.activity?.last_model,
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
      return haystack.includes(q)
    })
  }, [allAgents, activeRoleFilter, searchQuery])

  const groupedByRole = useMemo(() => {
    const groups: Record<string, ManagedAgent[]> = {}
    for (const agent of visibleAgents) {
      const role = agent.role || 'Other'
      if (!groups[role]) groups[role] = []
      groups[role].push(agent)
    }
    return groups
  }, [visibleAgents])

  const orderedRoles = useMemo(() => sortRoles(Object.keys(groupedByRole)), [groupedByRole])

  function toggleAgentExpand(agentId: number) {
    setExpandedAgents(prev => {
      const next = new Set(prev)
      if (next.has(agentId)) next.delete(agentId)
      else next.add(agentId)
      return next
    })
  }

  function toggleRoleCollapse(role: string) {
    setCollapsedRoles(prev => {
      const next = new Set(prev)
      if (next.has(role)) next.delete(role)
      else next.add(role)
      return next
    })
  }

  const filterChips = useMemo(() => sortRoles([...roleCounts.keys()]), [roleCounts])

  return {
    searchQuery, setSearchQuery,
    activeRoleFilter, setActiveRoleFilter,
    expandedAgents, collapsedRoles,
    roleCounts, visibleAgents, groupedByRole, orderedRoles, filterChips,
    toggleAgentExpand, toggleRoleCollapse,
  }
}
