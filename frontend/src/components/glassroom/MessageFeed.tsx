import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'
import type { AgentMessage } from '../../store/agentStore'
import { Bot, ChevronDown, Loader2, SplitSquareVertical } from 'lucide-react'
import type { ActiveAgent } from './liveStatus'
import type { MessageGroup } from './types'
import { AfterCloseSeparator, DateSeparator, NeedsSplitBanner, TypingIndicator } from './atoms'
import { SlackMessageGroup } from './MessageGroup'

/** The middle column: the grouped feed, who is typing, the scroll prompt and the status bar. */
export function MessageFeed({
  sprintId, messages, grouped, filteredCount, isFiltered, isConnected, connectionError, activeAgents,
  sprintStatus, containerRef, messagesEndRef, isAtBottom, handleScroll,
}: {
  sprintId: string
  messages: AgentMessage[]
  grouped: { date: string; groups: MessageGroup[] }[]
  filteredCount: number
  isFiltered: string
  isConnected: boolean
  connectionError: string | null
  activeAgents: ActiveAgent[]
  sprintStatus?: string
  containerRef: RefObject<HTMLDivElement>
  messagesEndRef: RefObject<HTMLDivElement>
  isAtBottom: boolean
  handleScroll: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="relative flex-1 flex flex-col min-w-0 overflow-visible lg:overflow-hidden">
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="min-h-[20rem] py-2 xl:pb-24 overflow-visible lg:flex-1 lg:overflow-x-hidden lg:overflow-y-auto"
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
                if (g.messages[0]?.type === ('needs_split' as AgentMessage['type'])) {
                  return <NeedsSplitBanner key={`${di}-${gi}`} message={g.messages[0]} />
                }
                return (
                  <div key={`${di}-${gi}`}>
                    {g.afterClose && <AfterCloseSeparator />}
                    <SlackMessageGroup group={g} sprintId={sprintId} activeAgents={activeAgents} />
                  </div>
                )
              })}
            </div>
          ))
        )}

        {activeAgents.map(a => <TypingIndicator key={a.key} agent={a} />)}
        <div ref={messagesEndRef} />
      </div>

      {/* Scroll-to-bottom prompt */}
      {!isAtBottom && messages.length > 0 && (
        <div className="absolute bottom-14 left-1/2 -translate-x-1/2">
          <button
            onClick={() => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })}
            className="flex items-center gap-1 px-3 py-1.5 rounded-full bg-brand-600/90 text-white text-[11px] font-medium shadow-lg hover:bg-brand-500 transition-colors"
          >
            <ChevronDown size={11} /> {t('glassroom.newMessages', { defaultValue: 'New messages' })}
          </button>
        </div>
      )}

      {/* Status bar */}
      <div className="px-4 py-1.5 border-t border-surface-700/60 shrink-0 flex items-center gap-3 bg-surface-900">
        <span className={`flex items-center gap-1.5 text-[10px] font-medium ${isConnected ? 'text-emerald-400' : 'text-gray-600'}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${isConnected ? 'bg-emerald-400' : 'bg-surface-600'}`} />
          {isConnected ? t('glassroom.connected') : t('glassroom.disconnected')}
        </span>
        <span className="text-[10px] text-gray-700">·</span>
        <span className="text-[10px] text-gray-600 tabular-nums">
          {isFiltered ? `${filteredCount} / ${messages.length}` : messages.length} {t('glassroom.eventsLabel', 'Events').toLowerCase()}
        </span>
        {activeAgents.length > 0 && (
          <>
            <span className="text-[10px] text-gray-700">·</span>
            <span className="text-[10px] text-emerald-400 flex items-center gap-1">
              <Loader2 size={9} className="animate-spin" />
              {t('glassroom.agentsWorkingCount', { count: activeAgents.length, defaultValue: '{{count}} working' })}
            </span>
          </>
        )}
        {sprintStatus === 'needs_split' && (
          <>
            <span className="text-[10px] text-gray-700">·</span>
            <span className="flex items-center gap-1 text-[10px] text-amber-400 font-medium">
              <SplitSquareVertical size={9} />
              {t('glassroom.needsSplit')}
            </span>
          </>
        )}
      </div>
    </div>
  )
}
