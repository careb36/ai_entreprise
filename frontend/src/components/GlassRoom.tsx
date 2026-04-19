/**
 * GlassRoom — enterprise agents room.
 *
 * Three-column layout: agent sidebar → message feed → detail panel.
 * Slack-style message grouping with rich type differentiation.
 * Handles needs_split events as first-class citizens.
 */

import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useAgentStore, AgentMessage } from '../store/agentStore'
import { useSprintBoard } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'
import {
  AlertTriangle, Bot, Check, ChevronDown, ChevronRight,
  Copy, Eye, Hash, Layers, Loader2, RotateCcw, Search,
  SplitSquareVertical, X, ZapOff,
} from 'lucide-react'

// ── Agent identity ────────────────────────────────────────────────────────────

interface AgentIdentity {
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

const ACTIVE_AGENT_TTL_MS = 90_000

function getAgent(key: string): AgentIdentity {
  return (
    AGENTS[key] ??
    AGENTS[Object.keys(AGENTS).find(k => k.toLowerCase() === key?.toLowerCase()) ?? ''] ??
    FALLBACK_AGENT
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTime(iso: string): string {
  try {
    return new Intl.DateTimeFormat('en', { hour: '2-digit', minute: '2-digit', hour12: true }).format(new Date(iso))
  } catch { return '' }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    const today     = new Date()
    const yesterday = new Date()
    yesterday.setDate(today.getDate() - 1)
    if (d.toDateString() === today.toDateString())     return 'Today'
    if (d.toDateString() === yesterday.toDateString()) return 'Yesterday'
    return new Intl.DateTimeFormat('en', { weekday: 'long', month: 'long', day: 'numeric' }).format(d)
  } catch { return '' }
}

interface MessageGroup {
  agent: string
  identity: AgentIdentity
  timestamp: string
  messages: AgentMessage[]
}

function groupMessages(messages: AgentMessage[]): { date: string; groups: MessageGroup[] }[] {
  const dayMap = new Map<string, AgentMessage[]>()
  for (const msg of messages) {
    const dateKey = msg.timestamp ? new Date(msg.timestamp).toDateString() : 'Unknown'
    if (!dayMap.has(dateKey)) dayMap.set(dateKey, [])
    dayMap.get(dateKey)!.push(msg)
  }

  const result: { date: string; groups: MessageGroup[] }[] = []
  for (const [, dayMsgs] of dayMap) {
    const groups: MessageGroup[] = []
    let current: MessageGroup | null = null
    for (const msg of dayMsgs) {
      const agentKey = msg.agent || msg.role || 'System'
      if (current && current.agent === agentKey) {
        current.messages.push(msg)
      } else {
        current = {
          agent: agentKey,
          identity: getAgent(agentKey),
          timestamp: msg.timestamp,
          messages: [msg],
        }
        groups.push(current)
      }
    }
    result.push({ date: formatDate(dayMsgs[0]?.timestamp || ''), groups })
  }
  return result
}

const EVENT_TYPES = [
  { value: '', label: 'All types' },
  { value: 'message',        label: 'Message'   },
  { value: 'agent_start',    label: 'Working'   },
  { value: 'agent_complete', label: 'Delivered' },
  { value: 'status',         label: 'Status'    },
  { value: 'error',          label: 'Error'     },
] as const

// ── Atoms ─────────────────────────────────────────────────────────────────────

function AgentAvatar({ identity, size = 'md' }: { identity: AgentIdentity; size?: 'sm' | 'md' | 'lg' }) {
  const dims = { sm: 'w-5 h-5 text-[9px]', md: 'w-8 h-8 text-xs', lg: 'w-10 h-10 text-sm' }[size]
  return (
    <div className={`${dims} ${identity.bg} ${identity.ring} ring-2 rounded-lg flex items-center justify-center font-bold text-white shrink-0 select-none`}>
      {identity.initials}
    </div>
  )
}

function TypeBadge({ type }: { type: string }) {
  const cfg: Record<string, { label: string; cls: string; spin?: boolean }> = {
    agent_start:    { label: 'working',   cls: 'bg-brand-500/15 text-brand-300 border-brand-500/25',     spin: true  },
    agent_complete: { label: 'delivered', cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/25'             },
    error:          { label: 'error',     cls: 'bg-red-500/15 text-red-300 border-red-500/25'                         },
    status:         { label: 'update',    cls: 'bg-yellow-500/15 text-yellow-300 border-yellow-500/25'                },
    needs_split:    { label: 'split',     cls: 'bg-amber-500/15 text-amber-300 border-amber-500/25'                   },
  }
  const c = cfg[type]
  if (!c) return null
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${c.cls}`}>
      {c.spin && <Loader2 size={9} className="animate-spin" />}
      {type === 'error' && <AlertTriangle size={9} />}
      {type === 'needs_split' && <SplitSquareVertical size={9} />}
      {c.label}
    </span>
  )
}

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await navigator.clipboard.writeText(text).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <button
      onClick={copy}
      className="opacity-0 group-hover/msg:opacity-100 transition-opacity p-1 rounded hover:bg-white/10"
      title="Copy"
    >
      {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} className="text-gray-500" />}
    </button>
  )
}

function PreviewCard({ preview, task }: { preview: string; task?: string }) {
  const [open, setOpen] = useState(false)
  if (!preview) return null
  return (
    <div className="mt-1.5 rounded-lg border border-surface-600/60 bg-surface-800/60 overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1.5 w-full text-left px-3 py-1.5 hover:bg-surface-700/40 transition-colors"
      >
        {open ? <ChevronDown size={11} className="text-gray-500" /> : <ChevronRight size={11} className="text-gray-500" />}
        <Eye size={11} className="text-brand-400" />
        <span className="text-[11px] text-gray-400 font-medium truncate flex-1">{task || 'View deliverable'}</span>
      </button>
      {open && (
        <div className="px-3 pb-2.5 pt-1 border-t border-surface-700/50">
          <pre className="text-[11px] text-gray-300 whitespace-pre-wrap break-words font-mono leading-relaxed max-h-48 overflow-y-auto">
            {preview}
          </pre>
        </div>
      )}
    </div>
  )
}

function DateSeparator({ date }: { date: string }) {
  return (
    <div className="flex items-center gap-3 py-2 px-4">
      <div className="flex-1 h-px bg-surface-700/60" />
      <span className="text-[10px] font-semibold text-gray-500 bg-surface-950 px-2.5 py-0.5 rounded-full border border-surface-700">
        {date}
      </span>
      <div className="flex-1 h-px bg-surface-700/60" />
    </div>
  )
}

function TypingIndicator({ identity }: { identity: AgentIdentity }) {
  return (
    <div className="flex items-center gap-2.5 px-4 py-2 animate-fade-in">
      <AgentAvatar identity={identity} size="sm" />
      <span className={`text-xs font-medium ${identity.color}`}>{identity.label}</span>
      <span className="flex gap-0.5 items-center">
        {[0, 1, 2].map(i => (
          <span
            key={i}
            className="w-1.5 h-1.5 rounded-full bg-gray-500"
            style={{ animation: `typingBounce 1.2s ease-in-out ${i * 0.15}s infinite` }}
          />
        ))}
      </span>
      <span className="text-[11px] text-gray-600 italic">thinking…</span>
    </div>
  )
}

/** NeedsSplit inline banner — shown in the feed when that event arrives. */
function NeedsSplitBanner({ message }: { message: AgentMessage }) {
  return (
    <div className="mx-4 my-2 flex items-start gap-3 p-3 rounded-lg border border-amber-500/30 bg-amber-950/20 animate-fade-in">
      <SplitSquareVertical size={15} className="text-amber-400 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-xs font-semibold text-amber-300">Sprint scope exceeded</p>
        <p className="text-[11px] text-amber-200/60 mt-0.5 break-words">{message.content}</p>
        <p className="text-[10px] text-amber-200/40 mt-0.5">{formatTime(message.timestamp)}</p>
      </div>
    </div>
  )
}

// ── Message row / group ───────────────────────────────────────────────────────

function MessageRow({ msg }: { msg: AgentMessage }) {
  const isError = msg.type === 'error'
  return (
    <div className="group/msg flex items-start gap-2 relative hover:bg-surface-800/30 rounded px-1 -mx-1 py-0.5 transition-colors">
      <span className="opacity-0 group-hover/msg:opacity-100 text-[10px] text-gray-600 min-w-[3rem] text-right pt-0.5 transition-opacity select-none shrink-0">
        {formatTime(msg.timestamp)}
      </span>
      <div className="flex-1 min-w-0">
        <p className={`text-[13px] whitespace-pre-wrap break-words leading-relaxed ${
          isError ? 'text-red-300' : 'text-gray-200'
        }`}>
          {msg.content}
        </p>
        {msg.preview && <PreviewCard preview={msg.preview} task={msg.task} />}
      </div>
      <CopyBtn text={msg.content} />
    </div>
  )
}

function SlackMessageGroup({ group }: { group: MessageGroup }) {
  const { identity, messages } = group
  const firstMsg = messages[0]
  const isError  = firstMsg?.type === 'error'

  return (
    <div className={`flex gap-3 px-4 py-1.5 animate-fade-in transition-colors ${
      isError ? 'bg-red-950/20 border-l-2 border-l-red-800/50' : 'hover:bg-surface-800/20'
    }`}>
      <div className="pt-0.5 shrink-0">
        <AgentAvatar identity={identity} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
          <span className={`font-bold text-[13px] ${identity.color}`}>{identity.label}</span>
          <TypeBadge type={firstMsg.type} />
          {firstMsg.task && (
            <span className="text-[11px] text-gray-500 truncate max-w-[180px]" title={firstMsg.task}>
              {firstMsg.task}
            </span>
          )}
          <span className="text-[11px] text-gray-600 ml-auto shrink-0">
            {formatTime(firstMsg.timestamp)}
          </span>
        </div>
        {messages.map(msg => <MessageRow key={msg.id} msg={msg} />)}
      </div>
    </div>
  )
}

// ── Left sidebar: sprint selector + agent roster ──────────────────────────────

function LeftSidebar({
  sprintId, scope, status, activeAgents, onlineAgents,
}: {
  sprintId: string
  scope?: string
  status?: string
  activeAgents: { key: string; identity: AgentIdentity }[]
  onlineAgents: AgentIdentity[]
}) {
  const SCOPE_COLOR: Record<string, string> = {
    S: 'text-emerald-400',
    M: 'text-brand-400',
    L: 'text-amber-400',
  }

  const STATUS_DOT: Record<string, string> = {
    active:      'bg-emerald-400 animate-pulse',
    needs_split: 'bg-amber-400 animate-pulse',
    completed:   'bg-gray-400',
  }

  return (
    <div className="w-52 flex-shrink-0 border-r border-surface-700/60 bg-surface-950 flex flex-col">
      {/* Sprint info */}
      <div className="p-3 border-b border-surface-700/60">
        <div className="flex items-center gap-2 mb-1">
          <Hash size={12} className="text-gray-500" />
          <span className="text-xs font-semibold text-gray-300 truncate">sprint-{sprintId}</span>
        </div>
        <div className="flex items-center gap-2">
          {scope && (
            <span className={`flex items-center gap-1 text-[10px] font-semibold ${SCOPE_COLOR[scope] ?? 'text-gray-400'}`}>
              <Layers size={9} /> {scope}
            </span>
          )}
          {status && (
            <span className="flex items-center gap-1 text-[10px] text-gray-500">
              <span className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[status] ?? 'bg-gray-600'}`} />
              {status}
            </span>
          )}
        </div>
      </div>

      {/* Active agents */}
      <div className="flex-1 overflow-y-auto py-2">
        <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider px-3 mb-1">
          Agents
        </p>
        {onlineAgents.length === 0 ? (
          <p className="text-[11px] text-gray-600 px-3 py-2 italic">Waiting for agents…</p>
        ) : (
          onlineAgents.map((identity, i) => {
            const isActive = activeAgents.some(a => a.identity.initials === identity.initials)
            return (
              <div
                key={`${identity.label}-${i}`}
                className="flex items-center gap-2 px-3 py-1.5 hover:bg-surface-800/40 transition-colors rounded"
              >
                <AgentAvatar identity={identity} size="sm" />
                <span className={`text-[11px] font-medium ${identity.color} flex-1 truncate`}>
                  {identity.label}
                </span>
                {isActive
                  ? <Loader2 size={10} className="text-emerald-400 animate-spin shrink-0" />
                  : <span className="w-1.5 h-1.5 rounded-full bg-surface-700 shrink-0" />
                }
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

// ── Right panel: agent detail ─────────────────────────────────────────────────

function RightPanel({ agentStates, messageCount, isConnected }: {
  agentStates: Record<string, { role: string; fsmState: string; currentActivity: string; color: string; lastSeen: string }>
  messageCount: number
  isConnected: boolean
}) {
  const entries = Object.entries(agentStates)

  return (
    <div className="w-52 flex-shrink-0 border-l border-surface-700/60 bg-surface-950 flex flex-col">
      <div className="p-3 border-b border-surface-700/60">
        <p className="text-[10px] font-semibold text-gray-600 uppercase tracking-wider">Live State</p>
      </div>

      <div className="flex-1 overflow-y-auto py-2 space-y-px">
        {entries.length === 0 ? (
          <p className="text-[11px] text-gray-600 px-3 py-2 italic">No agent activity yet.</p>
        ) : (
          entries.map(([role, state]) => {
            const identity = getAgent(role)
            return (
              <div key={role} className="px-3 py-2 hover:bg-surface-800/40 transition-colors">
                <div className="flex items-center gap-2 mb-1">
                  <AgentAvatar identity={identity} size="sm" />
                  <span className={`text-[11px] font-semibold ${identity.color} truncate flex-1`}>
                    {identity.label}
                  </span>
                </div>
                <div className="ml-7">
                  <span className={`inline-block text-[10px] font-medium px-1.5 py-0.5 rounded-full ${
                    state.fsmState === 'active'  ? 'bg-emerald-500/10 text-emerald-400' :
                    state.fsmState === 'waiting' ? 'bg-brand-500/10 text-brand-400'    :
                    state.fsmState === 'error'   ? 'bg-red-500/10 text-red-400'        :
                                                   'bg-surface-800 text-gray-600'
                  }`}>
                    {state.fsmState}
                  </span>
                  {state.currentActivity && (
                    <p className="text-[10px] text-gray-600 mt-0.5 break-words leading-tight line-clamp-2">
                      {state.currentActivity}
                    </p>
                  )}
                </div>
              </div>
            )
          })
        )}
      </div>

      {/* Stats footer */}
      <div className="p-3 border-t border-surface-700/60 space-y-1.5">
        <div className="flex items-center justify-between text-[10px]">
          <span className="text-gray-600">Stream</span>
          <span className={`flex items-center gap-1 font-medium ${isConnected ? 'text-emerald-400' : 'text-red-400'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-400 animate-pulse' : 'bg-red-400'}`} />
            {isConnected ? 'Live' : 'Off'}
          </span>
        </div>
        <div className="flex items-center justify-between text-[10px]">
          <span className="text-gray-600">Events</span>
          <span className="text-gray-400 font-medium tabular-nums">{messageCount}</span>
        </div>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function GlassRoom({ sprintId: propSprintId }: { sprintId: string }) {
  const { t } = useTranslation()

  const { messages, agentStates, isConnected, connectionError, connect, disconnect } = useAgentStore()
  const activeSprintId = useAppStore(s => s.activeSprintId)

  const numericId   = propSprintId ? parseInt(propSprintId, 10) : (activeSprintId ?? null)
  const { data: board } = useSprintBoard(isNaN(numericId as number) ? null : numericId)
  const sprint = board?.sprint

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const containerRef   = useRef<HTMLDivElement>(null)
  const [isAtBottom, setIsAtBottom] = useState(true)
  const [filterAgent, setFilterAgent]   = useState('')
  const [filterType,  setFilterType]    = useState('')
  const [searchText,  setSearchText]    = useState('')

  useEffect(() => {
    if (propSprintId) connect(propSprintId)
    return () => { disconnect() }
  }, [propSprintId])

  const handleScroll = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    setIsAtBottom(el.scrollHeight - el.scrollTop <= el.clientHeight + 50)
  }, [])

  useEffect(() => {
    if (isAtBottom) messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isAtBottom])

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

  const grouped = useMemo(() => groupMessages(filteredMessages), [filteredMessages])

  const activeAgents = useMemo(() => {
    const working = new Map<string, { identity: AgentIdentity; startedAt: number }>()
    const now = Date.now()
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
      .map(([key, v]) => ({ key, identity: v.identity }))
  }, [messages])

  const onlineAgents = useMemo(() => {
    const seen = new Map<string, AgentIdentity>()
    for (const msg of messages) {
      if (msg.agent && msg.agent !== 'System') seen.set(msg.agent, getAgent(msg.agent))
    }
    return [...seen.values()]
  }, [messages])

  const isFiltered = filterAgent || filterType || searchText

  return (
    <div className="flex flex-col h-full bg-surface-900">
      {/* Typing animation */}
      <style>{`
        @keyframes typingBounce {
          0%, 60%, 100% { transform: translateY(0) }
          30% { transform: translateY(-4px) }
        }
      `}</style>

      {/* ── Top bar ───────────────────────────────────────────────── */}
      <div className="flex items-center gap-2 px-4 h-11 border-b border-surface-700/60 bg-surface-900 shrink-0 flex-wrap">
        <Hash size={13} className="text-gray-600" />
        <span className="text-sm font-semibold text-gray-200">sprint-{propSprintId}</span>
        {sprint?.scope_size && (
          <span className={`text-[10px] font-semibold flex items-center gap-0.5 ${
            sprint.scope_size === 'S' ? 'text-emerald-400' :
            sprint.scope_size === 'L' ? 'text-amber-400' : 'text-brand-400'
          }`}>
            <Layers size={9} />
            {sprint.scope_size}
          </span>
        )}
        <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-400 animate-pulse' : 'bg-red-500'}`} />

        {/* Filters */}
        <div className="flex items-center gap-1.5 ml-3">
          <div className="relative">
            <Search size={11} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-600" />
            <input
              type="text"
              placeholder="Search…"
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
              className="pl-6 pr-5 py-1 text-[11px] bg-surface-800 border border-surface-700 rounded-md text-gray-300 placeholder-gray-600 focus:outline-none focus:border-brand-500 w-32 transition-colors"
            />
            {searchText && (
              <button onClick={() => setSearchText('')} className="absolute right-1.5 top-1/2 -translate-y-1/2">
                <X size={11} className="text-gray-500 hover:text-gray-300" />
              </button>
            )}
          </div>

          <select
            value={filterAgent}
            onChange={e => setFilterAgent(e.target.value)}
            className="py-1 px-2 text-[11px] bg-surface-800 border border-surface-700 rounded-md text-gray-300 focus:outline-none focus:border-brand-500"
          >
            <option value="">All agents</option>
            {onlineAgents.map(a => (
              <option key={a.label} value={a.label}>{a.label}</option>
            ))}
          </select>

          <select
            value={filterType}
            onChange={e => setFilterType(e.target.value)}
            className="py-1 px-2 text-[11px] bg-surface-800 border border-surface-700 rounded-md text-gray-300 focus:outline-none focus:border-brand-500"
          >
            {EVENT_TYPES.map(et => (
              <option key={et.value} value={et.value}>{et.label}</option>
            ))}
          </select>

          {isFiltered && (
            <button
              onClick={() => { setFilterAgent(''); setFilterType(''); setSearchText('') }}
              className="p-1 hover:bg-surface-700 rounded text-gray-500 hover:text-gray-300 transition-colors"
              title="Clear filters"
            >
              <RotateCcw size={11} />
            </button>
          )}
        </div>

        {/* Presence avatars */}
        {onlineAgents.length > 0 && (
          <div className="flex items-center gap-1.5 ml-auto">
            <div className="flex -space-x-1.5">
              {onlineAgents.slice(0, 5).map((a, i) => (
                <div
                  key={`${a.label}-${i}`}
                  title={a.label}
                  className={`w-5 h-5 ${a.bg} rounded-md flex items-center justify-center text-[8px] font-bold text-white ring-1 ring-surface-900`}
                >
                  {a.initials}
                </div>
              ))}
              {onlineAgents.length > 5 && (
                <div className="w-5 h-5 bg-surface-700 rounded-md flex items-center justify-center text-[8px] font-bold text-gray-400 ring-1 ring-surface-900">
                  +{onlineAgents.length - 5}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Connection error ─────────────────────��─────────────────── */}
      {connectionError && (
        <div className="mx-3 mt-2 p-2 bg-red-950/30 border border-red-800/40 rounded-lg shrink-0 flex items-center gap-2">
          <ZapOff size={12} className="text-red-400 shrink-0" />
          <p className="text-red-400 text-[11px] flex-1">{connectionError}</p>
          {propSprintId && (
            <button
              onClick={() => connect(propSprintId)}
              className="text-[11px] text-red-300 hover:text-white font-medium shrink-0 underline underline-offset-2"
            >
              {t('glassroom.retryConnection')}
            </button>
          )}
        </div>
      )}

      {/* ── Body: sidebar + feed + panel ──────────────────────────── */}
      <div className="flex flex-1 min-h-0">
        {/* Left sidebar */}
        <LeftSidebar
          sprintId={propSprintId}
          scope={sprint?.scope_size}
          status={sprint?.status}
          activeAgents={activeAgents}
          onlineAgents={onlineAgents}
        />

        {/* Message feed */}
        <div className="flex-1 flex flex-col min-w-0">
          <div
            ref={containerRef}
            onScroll={handleScroll}
            className="flex-1 overflow-y-auto py-2"
          >
            {messages.length === 0 ? (
              !isConnected && !connectionError ? (
                /* Connecting skeleton */
                <div className="flex flex-col gap-4 p-4 animate-pulse">
                  {[80, 60, 95, 45, 70].map((w, i) => (
                    <div key={i} className="flex gap-3 items-start">
                      <div className="w-7 h-7 rounded-lg bg-surface-700 shrink-0" />
                      <div className="flex-1 space-y-2">
                        <div className="h-2.5 bg-surface-700 rounded-full" style={{ width: `${w}%` }} />
                        <div className="h-2 bg-surface-800 rounded-full" style={{ width: `${w * 0.6}%` }} />
                      </div>
                    </div>
                  ))}
                  <p className="text-center text-xs text-gray-600 pt-2">{t('glassroom.connecting')}</p>
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center h-full text-center px-6">
                  <div className="w-14 h-14 rounded-2xl bg-surface-800 border border-surface-700 flex items-center justify-center mb-3">
                    <Bot size={24} className="text-brand-400 opacity-40" />
                  </div>
                  <p className="text-sm font-semibold text-gray-400">{t('glassroom.waiting')}</p>
                  <p className="text-xs text-gray-600 mt-1 max-w-[220px]">{t('glassroom.emptyState')}</p>
                </div>
              )
            ) : (
              grouped.map((day, di) => (
                <div key={di}>
                  <DateSeparator date={day.date} />
                  {day.groups.map((g, gi) => {
                    if (g.messages[0]?.type === ('needs_split' as any)) {
                      return <NeedsSplitBanner key={`${di}-${gi}`} message={g.messages[0]} />
                    }
                    return <SlackMessageGroup key={`${di}-${gi}`} group={g} />
                  })}
                </div>
              ))
            )}

            {activeAgents.map(a => <TypingIndicator key={a.key} identity={a.identity} />)}
            <div ref={messagesEndRef} />
          </div>

          {/* Scroll-to-bottom prompt */}
          {!isAtBottom && messages.length > 0 && (
            <div className="absolute bottom-14 left-1/2 -translate-x-1/2">
              <button
                onClick={() => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })}
                className="flex items-center gap-1 px-3 py-1.5 rounded-full bg-brand-600/90 text-white text-[11px] font-medium shadow-lg hover:bg-brand-500 transition-colors"
              >
                <ChevronDown size={11} /> New messages
              </button>
            </div>
          )}

          {/* Status bar */}
          <div className="px-4 py-1.5 border-t border-surface-700/60 shrink-0 flex items-center gap-3 bg-surface-900">
            <span className={`flex items-center gap-1.5 text-[10px] font-medium ${isConnected ? 'text-emerald-400' : 'text-gray-600'}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-400' : 'bg-surface-600'}`} />
              {isConnected ? 'Connected' : 'Disconnected'}
            </span>
            <span className="text-[10px] text-gray-700">·</span>
            <span className="text-[10px] text-gray-600 tabular-nums">
              {isFiltered ? `${filteredMessages.length} / ${messages.length}` : messages.length} events
            </span>
            {activeAgents.length > 0 && (
              <>
                <span className="text-[10px] text-gray-700">·</span>
                <span className="text-[10px] text-emerald-400 flex items-center gap-1">
                  <Loader2 size={9} className="animate-spin" />
                  {activeAgents.length} working
                </span>
              </>
            )}
            {sprint?.status === 'needs_split' && (
              <>
                <span className="text-[10px] text-gray-700">·</span>
                <span className="flex items-center gap-1 text-[10px] text-amber-400 font-medium">
                  <SplitSquareVertical size={9} />
                  Needs split
                </span>
              </>
            )}
          </div>
        </div>

        {/* Right panel */}
        <RightPanel
          agentStates={agentStates}
          messageCount={messages.length}
          isConnected={isConnected}
        />
      </div>
    </div>
  )
}
