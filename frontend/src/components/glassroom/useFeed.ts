import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { AgentMessage } from '../../store/agentStore'
import { authorKey, getAgent } from './identity'

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
      result = result.filter(m => getAgent(authorKey(m)).label.toLowerCase() === filterAgent.toLowerCase())
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
