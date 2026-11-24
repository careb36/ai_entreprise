import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { AgentMessage } from '../../store/agentStore'
import {
  AlertTriangle, Check, ChevronDown, ChevronRight, Copy, Eye, Loader2, SplitSquareVertical,
} from 'lucide-react'
import { FRESH_ACTIVITY_MS, type AgentIdentity } from './identity'
import type { ActiveAgent, GroupState } from './liveStatus'
import { formatElapsed, formatTime } from './format'

// ── Atoms ─────────────────────────────────────────────────────────────────────

export function AgentAvatar({ identity, size = 'md' }: { identity: AgentIdentity; size?: 'sm' | 'md' | 'lg' }) {
  const dims = { sm: 'w-5 h-5 text-[9px]', md: 'w-8 h-8 text-xs', lg: 'w-10 h-10 text-sm' }[size]
  return (
    <div className={`${dims} ${identity.bg} ${identity.ring} ring-2 rounded-lg flex items-center justify-center font-bold text-white shrink-0 select-none`}>
      {identity.initials}
    </div>
  )
}

const STATE_LOOK: Record<GroupState, { cls: string; spin?: boolean }> = {
  working:   { cls: 'bg-brand-500/15 text-brand-300 border-brand-500/25', spin: true },
  started:   { cls: 'bg-surface-800 text-gray-400 border-surface-600' },
  approved:  { cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/25' },
  delivered: { cls: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20' },
  refused:   { cls: 'bg-amber-500/15 text-amber-300 border-amber-500/25' },
  blocked:   { cls: 'bg-red-500/15 text-red-300 border-red-500/25' },
  error:     { cls: 'bg-red-500/15 text-red-300 border-red-500/25' },
  update:    { cls: 'bg-yellow-500/15 text-yellow-300 border-yellow-500/25' },
  split:     { cls: 'bg-amber-500/15 text-amber-300 border-amber-500/25' },
}

/** A message group's latest state (`liveStatus.groupState`); "working" only while the backend says so. */
export function StateBadge({ state }: { state: GroupState }) {
  const { t } = useTranslation()
  const look = STATE_LOOK[state]
  return (
    <span data-state={state} className={`inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded-full border font-medium ${look.cls}`}>
      {look.spin && <Loader2 size={9} className="animate-spin" />}
      {(state === 'error' || state === 'blocked') && <AlertTriangle size={9} />}
      {state === 'split' && <SplitSquareVertical size={9} />}
      {t(`glassroom.badgeLabels.${state}`)}
    </span>
  )
}

export function CopyBtn({ text }: { text: string }) {
  const { t } = useTranslation()
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
      title={t('glassroom.copy')}
    >
      {copied ? <Check size={13} className="text-emerald-400" /> : <Copy size={13} className="text-gray-500" />}
    </button>
  )
}

export function PreviewCard({ preview, task }: { preview: string; task?: string }) {
  const { t } = useTranslation()
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
        <span className="text-[11px] text-gray-400 font-medium truncate flex-1">{task || t('glassroom.viewDeliverable', { defaultValue: 'View deliverable' })}</span>
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

export function DateSeparator({ date }: { date: string }) {
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

/** An agent with a run in flight now (the backend's record), and on what. */
export function TypingIndicator({ agent }: { agent: ActiveAgent }) {
  const { t } = useTranslation()
  const { identity, elapsedMs, taskTitle, kind } = agent
  const isFresh = elapsedMs <= FRESH_ACTIVITY_MS
  const statusLabel = kind === 'review'
    ? t('glassroom.agentReviewing')
    : isFresh ? t('glassroom.agentActivelyThinking') : t('glassroom.agentStillWorking')

  return (
    <div className="flex items-center gap-2.5 px-4 py-2 animate-fade-in">
      <AgentAvatar identity={identity} size="sm" />
      <span className={`text-xs font-medium ${identity.color}`}>{identity.label}</span>
      {isFresh ? (
        <span className="flex gap-0.5 items-center">
          {[0, 1, 2].map(i => (
            <span
              key={i}
              className="w-1.5 h-1.5 rounded-full bg-gray-500"
              style={{ animation: `typingBounce 1.2s ease-in-out ${i * 0.15}s infinite` }}
            />
          ))}
        </span>
      ) : (
        <Loader2 size={12} className="text-gray-500 animate-spin" />
      )}
      <span className="text-[11px] text-gray-600 italic truncate">
        {statusLabel}{elapsedMs > 0 ? ` · ${formatElapsed(elapsedMs)}` : ''}{taskTitle ? ` · ${taskTitle}` : ''}
      </span>
    </div>
  )
}

/** Where the sprint closed in the feed: what follows happened after it (the integration, late events). */
export function AfterCloseSeparator() {
  const { t } = useTranslation()
  return (
    <div role="separator" className="flex items-center gap-3 py-2 px-4">
      <div className="flex-1 h-px bg-amber-700/40" />
      <span className="text-[10px] font-semibold text-amber-300/80 bg-surface-950 px-2.5 py-0.5 rounded-full border border-amber-700/40">
        {t('glassroom.afterClose')}
      </span>
      <div className="flex-1 h-px bg-amber-700/40" />
    </div>
  )
}

/** NeedsSplit inline banner — shown in the feed when that event arrives. */
export function NeedsSplitBanner({ message }: { message: AgentMessage }) {
  const { t } = useTranslation()
  return (
    <div className="mx-4 my-2 flex items-start gap-3 p-3 rounded-lg border border-amber-500/30 bg-amber-950/20 animate-fade-in">
      <SplitSquareVertical size={15} className="text-amber-400 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-xs font-semibold text-amber-300">{t('glassroom.scopeExceeded', { defaultValue: 'Sprint scope exceeded' })}</p>
        <p className="text-[11px] text-amber-200/60 mt-0.5 break-words">{message.content}</p>
        <p className="text-[10px] text-amber-200/40 mt-0.5">{formatTime(message.timestamp)}</p>
      </div>
    </div>
  )
}
