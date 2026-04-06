import { useEffect, useRef, useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useAgentStore, AgentMessage } from '../store/agentStore'
import {
  Bot, Copy, Check, ChevronDown, ChevronRight, Hash,
  CheckCircle2, AlertTriangle, Loader2, Eye, Sparkles,
} from 'lucide-react'

/* ─── Agent identity config ──────────────────────────────────────────────── */

interface AgentIdentity {
  initials: string
  bg: string          // Tailwind bg class for avatar circle
  ring: string        // Tailwind ring class for avatar focus ring
  label: string       // Display name
  color: string       // Text accent color
}

const AGENTS: Record<string, AgentIdentity> = {
  PO:           { initials: 'PO', bg: 'bg-purple-600',  ring: 'ring-purple-400/30', label: 'Product Owner',  color: 'text-purple-300'  },
  Architect:    { initials: 'AR', bg: 'bg-indigo-600',  ring: 'ring-indigo-400/30', label: 'Architect',      color: 'text-indigo-300'  },
  ScrumMaster:  { initials: 'SM', bg: 'bg-blue-600',    ring: 'ring-blue-400/30',   label: 'Scrum Master',   color: 'text-blue-300'    },
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

function getAgent(key: string): AgentIdentity {
  return AGENTS[key] ?? AGENTS[Object.keys(AGENTS).find(k => k.toLowerCase() === key?.toLowerCase()) ?? ''] ?? FALLBACK_AGENT
}

/* ─── Helpers ────────────────────────────────────────────────────────────── */

function formatTime(iso: string): string {
  try {
    return new Intl.DateTimeFormat('en', { hour: '2-digit', minute: '2-digit', hour12: true }).format(new Date(iso))
  } catch { return '' }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    const today = new Date()
    const yesterday = new Date(); yesterday.setDate(today.getDate() - 1)
    if (d.toDateString() === today.toDateString()) return 'Today'
    if (d.toDateString() === yesterday.toDateString()) return 'Yesterday'
    return new Intl.DateTimeFormat('en', { weekday: 'long', month: 'long', day: 'numeric' }).format(d)
  } catch { return '' }
}

/** Group consecutive messages from the same agent within the same minute. */
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

/* ─── Sub-components ─────────────────────────────────────────────────────── */

/** Slack-style avatar circle with initials. */
function AgentAvatar({ identity, size = 'md' }: { identity: AgentIdentity; size?: 'sm' | 'md' }) {
  const dim = size === 'sm' ? 'w-5 h-5 text-[9px]' : 'w-8 h-8 text-xs'
  return (
    <div className={`${dim} ${identity.bg} ${identity.ring} ring-2 rounded-lg flex items-center justify-center font-bold text-white shrink-0 select-none`}>
      {identity.initials}
    </div>
  )
}

/** Copy-to-clipboard button that appears on hover. */
function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await navigator.clipboard.writeText(text).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <button onClick={copy}
      className="opacity-0 group-hover/msg:opacity-100 transition-opacity p-1 rounded hover:bg-white/10"
      title="Copy">
      {copied ? <Check size={13} className="text-green-400" /> : <Copy size={13} className="text-gray-500" />}
    </button>
  )
}

/** Type badge shown next to agent name. */
function TypeBadge({ type }: { type: string }) {
  const map: Record<string, { label: string; cls: string; icon?: typeof CheckCircle2 }> = {
    agent_start:    { label: 'working',   cls: 'bg-blue-500/20 text-blue-300 border-blue-500/30',   icon: Loader2 },
    agent_complete: { label: 'delivered',  cls: 'bg-green-500/20 text-green-300 border-green-500/30', icon: CheckCircle2 },
    error:          { label: 'error',      cls: 'bg-red-500/20 text-red-300 border-red-500/30',       icon: AlertTriangle },
    status:         { label: 'update',     cls: 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30', icon: Sparkles },
  }
  const cfg = map[type]
  if (!cfg) return null
  const Icon = cfg.icon
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${cfg.cls}`}>
      {Icon && <Icon size={10} className={type === 'agent_start' ? 'animate-spin' : ''} />}
      {cfg.label}
    </span>
  )
}

/** Expandable preview card for deliverables. */
function PreviewCard({ preview, task }: { preview: string; task?: string }) {
  const [open, setOpen] = useState(false)
  if (!preview) return null
  return (
    <div className="mt-1.5 rounded-md border border-surface-600/60 bg-surface-800/60 overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1.5 w-full text-left px-3 py-1.5 hover:bg-surface-700/40 transition-colors"
      >
        {open ? <ChevronDown size={12} className="text-gray-500" /> : <ChevronRight size={12} className="text-gray-500" />}
        <Eye size={12} className="text-brand-400" />
        <span className="text-[11px] text-gray-400 font-medium truncate">{task || 'View deliverable'}</span>
      </button>
      {open && (
        <div className="px-3 pb-2.5 pt-1 border-t border-surface-700/50">
          <pre className="text-[11px] text-gray-300 whitespace-pre-wrap break-words font-mono leading-relaxed max-h-60 overflow-y-auto">
            {preview}
          </pre>
        </div>
      )}
    </div>
  )
}

/** Slack-style date separator. */
function DateSeparator({ date }: { date: string }) {
  return (
    <div className="flex items-center gap-3 py-2">
      <div className="flex-1 h-px bg-surface-700" />
      <span className="text-[11px] font-semibold text-gray-400 bg-surface-900 px-3 py-0.5 rounded-full border border-surface-700">
        {date}
      </span>
      <div className="flex-1 h-px bg-surface-700" />
    </div>
  )
}

/** Typing indicator — three bouncing dots. */
function TypingIndicator({ identity }: { identity: AgentIdentity }) {
  return (
    <div className="flex items-center gap-2.5 px-4 py-2 animate-fade-in">
      <AgentAvatar identity={identity} size="sm" />
      <span className={`text-xs font-medium ${identity.color}`}>{identity.label}</span>
      <span className="flex gap-0.5 items-center">
        {[0, 1, 2].map(i => (
          <span
            key={i}
            className="w-1.5 h-1.5 rounded-full bg-gray-400"
            style={{ animation: `typingBounce 1.2s ease-in-out ${i * 0.15}s infinite` }}
          />
        ))}
      </span>
      <span className="text-[11px] text-gray-500 italic">is working…</span>
    </div>
  )
}

/** Single message row inside a group (no avatar). */
function MessageRow({ msg }: { msg: AgentMessage }) {
  const isError = msg.type === 'error'
  return (
    <div className="group/msg flex items-start gap-2 relative hover:bg-surface-800/40 rounded px-1 -mx-1 py-0.5 transition-colors">
      {/* Hover timestamp (Slack shows it in the gutter) */}
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

/** A group of messages from the same agent — shows avatar + name once. */
function SlackMessageGroup({ group }: { group: MessageGroup }) {
  const { identity, messages } = group
  const firstMsg = messages[0]
  const isError = firstMsg?.type === 'error'

  return (
    <div className={`flex gap-3 px-4 py-1.5 animate-fade-in ${
      isError ? 'bg-red-950/20' : ''
    }`}>
      {/* Avatar column */}
      <div className="pt-0.5 shrink-0">
        <AgentAvatar identity={identity} />
      </div>

      {/* Content column */}
      <div className="flex-1 min-w-0">
        {/* Header: name + badge + timestamp */}
        <div className="flex items-center gap-2 mb-0.5">
          <span className={`font-bold text-[13px] ${identity.color} hover:underline cursor-default`}>
            {identity.label}
          </span>
          <TypeBadge type={firstMsg.type} />
          {firstMsg.task && (
            <span className="text-[11px] text-gray-500 truncate max-w-[200px]" title={firstMsg.task}>
              {firstMsg.task}
            </span>
          )}
          <span className="text-[11px] text-gray-500 ml-auto shrink-0">
            {formatTime(firstMsg.timestamp)}
          </span>
        </div>

        {/* Messages */}
        {messages.map(msg => <MessageRow key={msg.id} msg={msg} />)}
      </div>
    </div>
  )
}

/* ─── Main component ─────────────────────────────────────────────────────── */

export default function GlassRoom({ sprintId: propSprintId }: { sprintId: string }) {
  const { t } = useTranslation()
  const { messages, isConnected, connectionError, connect, disconnect } = useAgentStore()
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (propSprintId) connect(propSprintId)
    return () => { disconnect() }
  }, [propSprintId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // Group messages by date → by consecutive agent.
  const grouped = useMemo(() => groupMessages(messages), [messages])

  // Find agents currently "working" (last message is agent_start).
  const activeAgents = useMemo(() => {
    const working = new Map<string, AgentIdentity>()
    for (const msg of messages) {
      if (msg.type === 'agent_start') working.set(msg.agent, getAgent(msg.agent))
      else if (msg.type === 'agent_complete' || msg.type === 'error') working.delete(msg.agent)
    }
    return [...working.values()]
  }, [messages])

  // Online agents (unique agents who posted).
  const onlineAgents = useMemo(() => {
    const seen = new Map<string, AgentIdentity>()
    for (const msg of messages) {
      if (msg.agent && msg.agent !== 'System') seen.set(msg.agent, getAgent(msg.agent))
    }
    return [...seen.values()]
  }, [messages])

  return (
    <div className="flex flex-col h-full bg-surface-900">
      {/* ── Typing CSS (injected once) ──────────────────────────── */}
      <style>{`
        @keyframes typingBounce {
          0%, 60%, 100% { transform: translateY(0) }
          30% { transform: translateY(-4px) }
        }
      `}</style>

      {/* ── Channel header bar ──────────────────────────────────── */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-surface-700 bg-surface-900 shrink-0">
        <Hash size={14} className="text-gray-500" />
        <span className="font-semibold text-sm text-gray-200">sprint-{propSprintId}</span>
        <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-500'}`} />
        {/* Online agent avatars (Slack-style presence row) */}
        {onlineAgents.length > 0 && (
          <div className="flex items-center gap-1 ml-auto">
            <span className="text-[10px] text-gray-500 mr-1">{onlineAgents.length} agents</span>
            <div className="flex -space-x-1.5">
              {onlineAgents.slice(0, 6).map(a => (
                <div key={a.initials} title={a.label}
                  className={`w-5 h-5 ${a.bg} rounded-md flex items-center justify-center text-[8px] font-bold text-white ring-1 ring-surface-900`}>
                  {a.initials}
                </div>
              ))}
              {onlineAgents.length > 6 && (
                <div className="w-5 h-5 bg-gray-700 rounded-md flex items-center justify-center text-[8px] font-bold text-gray-300 ring-1 ring-surface-900">
                  +{onlineAgents.length - 6}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* ── Connection error banner ────────────────────────────── */}
      {connectionError && (
        <div className="mx-4 mt-2 p-2 bg-red-900/30 border border-red-800/50 rounded-lg shrink-0">
          <p className="text-red-400 text-xs flex items-center gap-1.5">
            <AlertTriangle size={12} /> {connectionError}
          </p>
        </div>
      )}

      {/* ── Message feed ───────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto py-2">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-500 py-8">
            <div className="w-16 h-16 rounded-2xl bg-surface-800 border border-surface-700 flex items-center justify-center mb-4">
              <Bot size={28} className="text-brand-400 opacity-50" />
            </div>
            <p className="text-sm font-medium text-gray-400">
              {t('glassroom.waiting', 'Waiting for agent activity…')}
            </p>
            <p className="text-xs mt-1 text-gray-600 max-w-[260px] text-center">
              All your people and AI agents working together. Messages will appear here in real-time.
            </p>
          </div>
        ) : (
          <>
            {grouped.map((day, di) => (
              <div key={di}>
                <DateSeparator date={day.date} />
                {day.groups.map((g, gi) => (
                  <SlackMessageGroup key={`${di}-${gi}`} group={g} />
                ))}
              </div>
            ))}
          </>
        )}

        {/* Typing indicators for active agents */}
        {activeAgents.map(a => <TypingIndicator key={a.initials} identity={a} />)}

        <div ref={messagesEndRef} />
      </div>

      {/* ── Footer ─────────────────────────────────────────────── */}
      <div className="px-4 py-1.5 border-t border-surface-700 shrink-0 flex items-center gap-3">
        <div className="flex items-center gap-1.5 text-[11px] text-gray-500">
          <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-green-400' : 'bg-red-500'}`} />
          <span>{isConnected ? 'Connected' : 'Disconnected'}</span>
        </div>
        <span className="text-[11px] text-gray-600">•</span>
        <span className="text-[11px] text-gray-500">{messages.length} messages</span>
        {activeAgents.length > 0 && (
          <>
            <span className="text-[11px] text-gray-600">•</span>
            <span className="text-[11px] text-green-400 flex items-center gap-1">
              <Loader2 size={10} className="animate-spin" />
              {activeAgents.length} working
            </span>
          </>
        )}
      </div>
    </div>
  )
}
