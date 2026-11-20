/**
 * GlassRoom — enterprise agents room.
 *
 * Three-column layout: agent sidebar → message feed → detail panel.
 * Slack-style message grouping with rich type differentiation.
 * Handles needs_split events as first-class citizens.
 *
 * The parts live in ./glassroom/: the columns, the panels, the atoms, and the
 * hooks that derive the feed and the sprint's state from the stream.
 */

import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useAgentStore } from '../store/agentStore'
import { useSprintBoard, useQuotas } from '../hooks/useQueries'
import { useAppStore } from '../store/useAppStore'
import { ZapOff } from 'lucide-react'
import { getEventTypes } from './glassroom/format'
import { groupMessages } from './glassroom/groupMessages'
import { useAgentPresence, useAutoScroll, useClockTick, useFeedFilters } from './glassroom/useFeed'
import {
  useAutonomyTree, useHealthEntries, useQuotaTelemetry, useSprintTasks,
} from './glassroom/useSprintInsights'
import { TopBar } from './glassroom/TopBar'
import { LeftSidebar } from './glassroom/LeftSidebar'
import { MessageFeed } from './glassroom/MessageFeed'
import { RightPanel } from './glassroom/RightPanel'

export default function GlassRoom({ sprintId: propSprintId }: { sprintId: string }) {
  const { t } = useTranslation()
  const eventTypes = useMemo(() => getEventTypes(t), [t])

  const { messages, agentStates, isConnected, connectionError, connect, disconnect, memoryHits, lastRetro } = useAgentStore()
  const activeSprintId = useAppStore(s => s.activeSprintId)
  const companyId = useAppStore(s => s.companyId)

  const numericId   = propSprintId ? parseInt(propSprintId, 10) : (activeSprintId ?? null)
  const { data: board } = useSprintBoard(isNaN(numericId as number) ? null : numericId)
  const { data: quotas } = useQuotas(companyId)
  const sprint = board?.sprint

  useEffect(() => {
    if (propSprintId) connect(propSprintId)
    return () => { disconnect() }
  }, [propSprintId])

  const { messagesEndRef, containerRef, isAtBottom, handleScroll } = useAutoScroll(messages)
  const clockTick = useClockTick()
  const filters = useFeedFilters(messages)
  const { filteredMessages, isFiltered } = filters

  const grouped = useMemo(() => groupMessages(filteredMessages), [filteredMessages])
  const { activeAgents, onlineAgents } = useAgentPresence(messages, clockTick)
  const sprintTasks = useSprintTasks(board)
  const quotaTelemetry = useQuotaTelemetry(quotas)
  const healthEntries = useHealthEntries({ agentStates, clockTick, isConnected, messages, sprintTasks, memoryHits, lastRetro, t })
  const autonomyTree = useAutonomyTree(messages, sprintTasks, t)

  return (
    <div className="flex flex-col min-h-full xl:h-full bg-surface-900">
      {/* Typing animation */}
      <style>{`
        @keyframes typingBounce {
          0%, 60%, 100% { transform: translateY(0) }
          30% { transform: translateY(-4px) }
        }
      `}</style>

      {/* ── Top bar ───────────────────────────────────────────────── */}
      <TopBar
        sprintId={propSprintId}
        scopeSize={sprint?.scope_size}
        isConnected={isConnected}
        filters={filters}
        eventTypes={eventTypes}
        onlineAgents={onlineAgents}
      />

      {/* ── Connection error ───────────────────────────────────────── */}
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
      <div className="flex flex-1 min-h-0 flex-col overflow-visible lg:grid lg:grid-cols-[15rem_minmax(0,1fr)_22rem] 2xl:grid-cols-[17rem_minmax(0,1.2fr)_24rem] lg:overflow-hidden">
        {/* Left sidebar */}
        <LeftSidebar
          sprintId={propSprintId}
          scope={sprint?.scope_size}
          status={sprint?.status}
          activeAgents={activeAgents}
          onlineAgents={onlineAgents}
        />

        {/* Message feed */}
        <MessageFeed
          sprintId={propSprintId}
          messages={messages}
          grouped={grouped}
          filteredCount={filteredMessages.length}
          isFiltered={isFiltered}
          isConnected={isConnected}
          connectionError={connectionError}
          activeAgents={activeAgents}
          sprintStatus={sprint?.status}
          containerRef={containerRef}
          messagesEndRef={messagesEndRef}
          isAtBottom={isAtBottom}
          handleScroll={handleScroll}
        />

        {/* Right panel */}
        <RightPanel
          sprintId={propSprintId}
          agentStates={agentStates}
          messageCount={messages.length}
          isConnected={isConnected}
          sprintTasks={sprintTasks}
          healthEntries={healthEntries}
          autonomyTree={autonomyTree}
          quotaTelemetry={quotaTelemetry}
          lastRetro={lastRetro}
        />
      </div>
    </div>
  )
}
