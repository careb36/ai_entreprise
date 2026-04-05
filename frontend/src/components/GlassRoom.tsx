import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAgentStore, AgentMessage } from '../store/agentStore'
import {
  Bot, User, Shield, AlertCircle, Clock, Copy, Check,
  Cpu, Database, TestTube, Wrench, Zap
} from 'lucide-react'

const AGENT_CONFIG: Record<string, { icon: typeof Bot; color: string; bg: string; label: string }> = {
  PO:           { icon: Shield,    color: 'text-purple-400', bg: 'bg-purple-900/30', label: 'Product Owner' },
  ScrumMaster:  { icon: Bot,       color: 'text-blue-400',   bg: 'bg-blue-900/30',   label: 'Scrum Master' },
  Dev:          { icon: User,      color: 'text-green-400',  bg: 'bg-green-900/30',  label: 'Developer' },
  Backend:      { icon: Cpu,       color: 'text-cyan-400',   bg: 'bg-cyan-900/30',   label: 'Backend Dev' },
  Frontend:     { icon: Zap,       color: 'text-yellow-400', bg: 'bg-yellow-900/30', label: 'Frontend Dev' },
  QA:           { icon: TestTube,  color: 'text-pink-400',   bg: 'bg-pink-900/30',   label: 'QA' },
  DevOps:       { icon: Wrench,    color: 'text-orange-400', bg: 'bg-orange-900/30', label: 'DevOps' },
  DataEngineer: { icon: Database,  color: 'text-teal-400',   bg: 'bg-teal-900/30',   label: 'Data Engineer' },
  System:       { icon: AlertCircle, color: 'text-yellow-400', bg: 'bg-yellow-900/30', label: 'System' },
}

const FALLBACK = { icon: Bot, color: 'text-gray-400', bg: 'bg-gray-800/50', label: 'Agent' }

function useRelativeTime(iso: string, lang: string) {
  try {
    const diff = Date.now() - new Date(iso).getTime()
    const secs = Math.floor(diff / 1000)
    if (secs < 10) return 'just now'
    if (secs < 60) return `${secs}s ago`
    const mins = Math.floor(secs / 60)
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    return new Intl.DateTimeFormat(lang, { month: 'short', day: 'numeric' }).format(new Date(iso))
  } catch { return '' }
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    await navigator.clipboard.writeText(text).catch(() => {})
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }
  return (
    <button onClick={copy}
      className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-white/10"
      aria-label="Copy message">
      {copied ? <Check size={12} className="text-green-400" /> : <Copy size={12} className="text-gray-500" />}
    </button>
  )
}

function MessageBubble({ message }: { message: AgentMessage }) {
  const { i18n } = useTranslation()
  const cfg  = AGENT_CONFIG[message.agent] ?? FALLBACK
  const Icon = cfg.icon
  const isError = message.type === 'error'
  const relTime = useRelativeTime(message.timestamp, i18n.language)

  return (
    <div className={`group flex gap-3 p-3 rounded-lg transition-colors ${
      isError ? 'bg-red-900/30 border border-red-800/50' : cfg.bg
    } animate-fade-in`}>
      <div className={`mt-0.5 shrink-0 ${isError ? 'text-red-400' : cfg.color}`}>
        <Icon size={16} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className={`font-semibold text-xs ${isError ? 'text-red-400' : cfg.color}`}>
            {cfg.label}
          </span>
          {message.type === 'action' && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-brand-700/40 text-brand-300">action</span>
          )}
          <span className="text-[11px] text-gray-500 flex items-center gap-1 ml-auto">
            <Clock size={10} />
            {relTime}
          </span>
          <CopyButton text={message.content} />
        </div>
        <p className={`text-sm whitespace-pre-wrap break-words leading-relaxed ${
          isError ? 'text-red-300' : 'text-gray-200'
        }`}>
          {message.content}
        </p>
      </div>
    </div>
  )
}

export default function GlassRoom({ sprintId: propSprintId }: { sprintId: string }) {
  const { t } = useTranslation()
  const { messages, isConnected, connectionError, connect, disconnect, sprintId: storeSprintId } = useAgentStore()
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (propSprintId) connect(propSprintId)
    return () => { disconnect() }
  }, [propSprintId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const errorCount = messages.filter(m => m.type === 'error').length

  return (
    <div className="flex flex-col h-full bg-surface-900 rounded-lg border border-surface-700">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Bot className="text-brand-400 shrink-0" size={18} />
          <h2 className="font-semibold text-white text-sm truncate">{t('glassroom.title')}</h2>
          <span className="text-xs text-gray-400 hidden sm:inline truncate">— {t('glassroom.subtitle')}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {errorCount > 0 && (
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-red-900/50 text-red-400 border border-red-800">
              {errorCount} err
            </span>
          )}
          <span className={`px-2.5 py-0.5 rounded-full text-[11px] font-medium flex items-center gap-1 ${
            isConnected
              ? 'bg-status-online/15 text-status-online border border-status-online/30'
              : 'bg-red-900/30 text-red-400 border border-red-800/40'
          }`}>
            <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-status-online animate-pulse' : 'bg-red-500'}`} />
            {isConnected ? t('glassroom.connected') : t('glassroom.disconnected')}
          </span>
        </div>
      </div>

      {connectionError && (
        <div className="mx-3 mt-3 p-2.5 bg-red-900/30 border border-red-800/50 rounded-lg shrink-0">
          <p className="text-red-400 text-xs">{connectionError}</p>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-500 py-8">
            <Bot size={40} className="mb-3 opacity-30" />
            <p className="text-sm">{t('glassroom.waiting')}</p>
            <p className="text-xs mt-1 opacity-70">{t('glassroom.eventsHint', 'Events will appear here in real-time')}</p>
          </div>
        ) : (
          messages.map((msg) => <MessageBubble key={msg.id} message={msg} />)
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Footer */}
      <div className="px-3 py-2 border-t border-surface-700 shrink-0">
        <div className="flex items-center gap-2 text-[11px] text-gray-500">
          <span>{messages.length} {t('glassroom.messages', 'messages')}</span>
          <span>•</span>
          <span className="truncate">{t('glassroom.sprintId')}: {storeSprintId || '—'}</span>
        </div>
      </div>
    </div>
  )
}
