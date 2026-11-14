import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { AgentMessage } from '../../store/agentStore'
import { ACTIVE_AGENT_TTL_MS, getAgent, type AgentIdentity } from './identity'

/** The current time, refreshed every five seconds for elapsed and freshness labels. */
export function useClockTick() {
  const [clockTick, setClockTick] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setClockTick(Date.now()), 5000)
    return () => window.clearInterval(timer)
  }, [])

  return clockTick
}

/** Keeps the feed pinned to its newest message while the reader is at the bottom. */
export function useAutoScroll(messages: AgentMessage[]) {
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const containerRef   = useRef<HTMLDivElement>(null)
  const [isAtBottom, setIsAtBottom] = useState(true)

  const handleScroll = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    setIsAtBottom(el.scrollHeight - el.scrollTop <= el.clientHeight + 50)
  }, [])

  useEffect(() => {
    if (isAtBottom) {
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
      })
    }
  }, [messages, isAtBottom])

  return { messagesEndRef, containerRef, isAtBottom, handleScroll }
}

/** The feed's agent, type and text filters, and the messages they let through. */
export function useFeedFilters(messages: AgentMessage[]) {
  const [filterAgent, setFilterAgent]   = useState('')
  const [filterType,  setFilterType]    = useState('')
  const [searchText,  setSearchText]    = useState('')

  const filteredMessages = useMemo(() => {
    let result = messages
    if (filterAgent) {
      result = result.filter(m => {
        const key = m.agent || m.role || ''
        return key.toLowerCase() === filterAgent.toLowerCase() ||
               getAgent(key).label.toLowerCase() === filterAgent.toLowerCase()
      })
    }
    if (filterType) result = result.filter(m => m.type === filterType)
    if (searchText) {
      const term = searchText.toLowerCase()
      result = result.filter(m => m.content.toLowerCase().includes(term))
    }
    return result
  }, [messages, filterAgent, filterType, searchText])

  const isFiltered = filterAgent || filterType || searchText

  const clearFilters = () => { setFilterAgent(''); setFilterType(''); setSearchText('') }

  return {
    filterAgent, setFilterAgent,
    filterType, setFilterType,
    searchText, setSearchText,
    filteredMessages, isFiltered, clearFilters,
  }
}

export type FeedFilters = ReturnType<typeof useFeedFilters>

/** Agents working now (started and not finished within the TTL) and every agent seen in the feed. */
export function useAgentPresence(messages: AgentMessage[], clockTick: number) {
  const activeAgents = useMemo(() => {
    const working = new Map<string, { identity: AgentIdentity; startedAt: number }>()
    const now = clockTick
    for (const msg of messages) {
      const key = msg.agent || msg.role || 'System'
      if (!key || key === 'System') continue
      if (msg.type === 'agent_start') {
        working.set(key, { identity: getAgent(key), startedAt: Date.parse(msg.timestamp || '') || now })
      } else if (msg.type === 'agent_complete' || msg.type === 'error' || msg.type === 'status') {
        working.delete(key)
      }
    }
    return [...working.entries()]
      .filter(([, v]) => now - v.startedAt <= ACTIVE_AGENT_TTL_MS)
      .map(([key, v]) => ({ key, identity: v.identity, elapsedMs: now - v.startedAt }))
  }, [messages, clockTick])

  const onlineAgents = useMemo(() => {
    const seen = new Map<string, AgentIdentity>()
    for (const msg of messages) {
      if (msg.agent && msg.agent !== 'System') seen.set(msg.agent, getAgent(msg.agent))
    }
    return [...seen.values()]
  }, [messages])

  return { activeAgents, onlineAgents }
}
