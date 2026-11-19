/**
 * Sprint page — one page per sprint at `/sprints/:id`, with six tabs:
 * Overview (`/sprints/:id`, with the claimed-versus-verified progress of
 * issue #531), Board (`/board`), Live (`/live`), Conversation
 * (`/conversation`), Board notes (`/notes`, the sprint board of issue #439)
 * and Analytics (`/analytics`). An unknown tab falls back to Overview.
 *
 * The page validates the sprint before rendering any tab, and only remembers
 * it as the active sprint when it belongs to the selected company; a sprint of
 * another company stays read-only.
 */
import React, { useEffect } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { MonitorPlay, Radio, RefreshCw } from 'lucide-react'
import { useAppStore } from '../store/useAppStore'
import { useAgentStore } from '../store/agentStore'
import GlassRoom from '../components/GlassRoom'
import { SprintTaskViews } from '../components/SprintTaskViews'
import { AnalyticsDashboard } from '../components/AnalyticsDashboard'
import { Conversation } from '../components/Conversation'
import { BoardNotes } from '../components/BoardNotes'
import SprintOverview from './Dashboard'
import { useCurrentRole, useSprintBoard } from '../hooks/useQueries'
import { ApiError } from '../services/api'
import { AutonomyControl, SprintControls, SprintStatusBanner } from '../components/SprintControls'
import { SprintTeamChips } from '../components/SprintTeam'
import { SprintRelations } from '../components/SprintRelations'
import { SprintIntegration } from '../components/SprintIntegration'
import { SprintProgress } from '../components/SprintProgress'
import { parseRouteId } from '../lib/routeId'
import { CountPill, PageHeader, SprintStatusBadge } from '../components/ui'

export const SPRINT_TABS = ['overview', 'board', 'live', 'conversation', 'notes', 'analytics'] as const
export type SprintTab = typeof SPRINT_TABS[number]

const TAB_LABELS: Record<SprintTab, { key: string; defaultValue: string }> = {
  overview: { key: 'sprint.tabs.overview', defaultValue: 'Overview' },
  board: { key: 'sprint.tabs.board', defaultValue: 'Board' },
  live: { key: 'sprint.tabs.live', defaultValue: 'Live' },
  conversation: { key: 'sprint.tabs.conversation', defaultValue: 'Conversation' },
  notes: { key: 'sprint.tabs.notes', defaultValue: 'Board notes' },
  analytics: { key: 'sprint.tabs.analytics', defaultValue: 'Analytics' },
}

export function sprintPath(id: number | string, tab: SprintTab = 'overview') {
  return tab === 'overview' ? `/sprints/${id}` : `/sprints/${id}/${tab}`
}

function isSprintTab(value: string): value is SprintTab {
  return (SPRINT_TABS as readonly string[]).includes(value)
}

function SprintTabs({ sprintId, selected }: { sprintId: number; selected: SprintTab }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <div role="tablist" aria-label={t('sprint.views', { defaultValue: 'Sprint views' })}
      className="flex min-w-0 gap-1 overflow-x-auto"
      onKeyDown={event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
        event.preventDefault()
        const current = SPRINT_TABS.indexOf(selected)
        const next = event.key === 'Home' ? 0
          : event.key === 'End' ? SPRINT_TABS.length - 1
          : (current + (event.key === 'ArrowLeft' ? -1 : 1) + SPRINT_TABS.length) % SPRINT_TABS.length
        navigate(sprintPath(sprintId, SPRINT_TABS[next]))
        event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus()
      }}>
      {SPRINT_TABS.map(tab => {
        const isSelected = tab === selected
        return (
          <Link key={tab} id={`sprint-tab-${tab}`} role="tab" to={sprintPath(sprintId, tab)}
            aria-selected={isSelected} aria-controls="sprint-panel" tabIndex={isSelected ? 0 : -1}
            className={`whitespace-nowrap rounded-t-md border-b-2 px-3 py-2 text-[13px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand-500 motion-reduce:transition-none ${
              isSelected
                ? 'border-brand-500 text-white'
                : 'border-transparent text-gray-400 hover:bg-surface-800/60 hover:text-white'
            }`}>
            {t(TAB_LABELS[tab].key, { defaultValue: TAB_LABELS[tab].defaultValue })}
          </Link>
        )
      })}
    </div>
  )
}

function LivePanel({ sprintId }: { sprintId: number }) {
  const { t } = useTranslation()
  const isSSEConnected = useAgentStore(s => s.isConnected)
  const messageCount = useAgentStore(s => s.messages.length)
  return (
    <div className="panel flex min-h-[32rem] flex-col overflow-visible xl:h-[calc(100vh-14rem)] xl:overflow-hidden">
      <div className="panel-header">
        <MonitorPlay size={14} className="text-brand-400" aria-hidden="true" />
        <span aria-hidden="true"
          className={`h-1.5 w-1.5 rounded-full ${isSSEConnected ? 'animate-pulse bg-green-400 motion-reduce:animate-none' : 'bg-red-500'}`} />
        <span className="text-xs text-gray-300">
          {isSSEConnected
            ? t('sprint.streamConnected', { defaultValue: 'Stream connected' })
            : t('sprint.streamDisconnected', { defaultValue: 'Stream disconnected' })}
        </span>
        {messageCount > 0 && (
          <CountPill count={messageCount} label={t('sprint.messageCount', { count: messageCount, defaultValue: '{{count}} messages' })} />
        )}
        <span className="text-meta ml-auto hidden items-center gap-1 sm:flex">
          <Radio size={11} aria-hidden="true" /> {t('workspace.aiAgentsActive', { defaultValue: 'AI Agents Active' })}
        </span>
      </div>
      <div className="min-h-0 flex-1">
        <GlassRoom sprintId={String(sprintId)} />
      </div>
    </div>
  )
}

function BoardPanel({ sprintId, companyId }: { sprintId: number; companyId: number | null }) {
  const { t } = useTranslation()
  return (
    <div className="panel flex min-h-[34rem] flex-col overflow-hidden">
      <p className="text-meta border-b border-surface-700 px-3 py-2">
        {t('workspace.boardOpsHint', { defaultValue: 'Drag tasks to change status or reassign' })}
      </p>
      <div className="min-h-0 flex-1">
        <SprintTaskViews sprintId={sprintId} companyId={companyId} />
      </div>
    </div>
  )
}

export const SprintWorkspace: React.FC = () => {
  const { id, tab: rawTab } = useParams<{ id: string; tab?: string }>()
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const setActiveSprint = useAppStore(state => state.setActiveSprint)
  const sprintId = parseRouteId(id)
  const requestedTab = rawTab?.toLowerCase()
  const tab: SprintTab = requestedTab && isSprintTab(requestedTab) ? requestedTab : 'overview'
  const boardQuery = useSprintBoard(sprintId)
  // Before the early returns below: a hook must run on every render.
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const notFound = boardQuery.error instanceof ApiError && boardQuery.error.status === 404
  const noAccess = boardQuery.error instanceof ApiError && boardQuery.error.status === 403
  const terminalMessage = noAccess
    ? t('workspace.noAccess', { id, defaultValue: 'You no longer have access to sprint #{{id}}.' })
    : notFound
      ? t('workspace.notFound', { id, defaultValue: 'Sprint #{{id}} was not found.' })
      : null

  useEffect(() => {
    if (sprintId !== null && boardQuery.data && !boardQuery.isError && boardQuery.data.sprint.company_id === companyId) {
      setActiveSprint(sprintId)
    }
  }, [sprintId, boardQuery.data, boardQuery.isError, companyId, setActiveSprint])

  const sprintsLabel = t('nav.sprints', { defaultValue: 'Sprints' })
  if (sprintId === null || (boardQuery.isError && !boardQuery.data)) {
    const message = sprintId === null
      ? t('workspace.invalidId', { id, defaultValue: 'Invalid sprint ID: {{id}}.' })
      : terminalMessage ?? t('workspace.loadError', { id, defaultValue: 'Failed to load sprint #{{id}}.' })
    return (
      <div className="page-container max-w-6xl space-y-3">
        <div role="alert" className="alert-error">
          {message}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {sprintId !== null && !notFound && !noAccess && (
            <button type="button" className="btn-tool" onClick={() => boardQuery.refetch()}>
              <RefreshCw size={13} aria-hidden="true" />{t('common.retry', { defaultValue: 'Retry' })}
            </button>
          )}
          <Link to="/sprints" className="btn-tool border border-surface-700">{sprintsLabel}</Link>
        </div>
      </div>
    )
  }

  // Normalise `/sprints/:id/overview` and unknown tabs to the canonical Overview URL.
  if (rawTab !== undefined && tab === 'overview') {
    return <Navigate to={sprintPath(sprintId)} replace />
  }

  const board = boardQuery.data
  const sprintName = board?.sprint.sprint_name || t('workspace.sprintLabel', { id: sprintId, defaultValue: 'Sprint #{{id}}' })
  const wide = tab === 'board' || tab === 'live'

  return (
    <div className="bg-surface-950 px-4 pb-24 pt-4 text-white lg:px-6">
      <div className={`mx-auto min-w-0 space-y-4 ${wide ? 'max-w-[1920px]' : 'max-w-6xl'}`}>
        <PageHeader
          breadcrumbs={[{ label: sprintsLabel, to: '/sprints' }, { label: sprintName }]}
          title={<span className="block truncate">{sprintName}</span>}
          subtitle={board?.sprint.status ? <SprintStatusBadge status={board.sprint.status} integrationState={board.sprint.integration_state} /> : undefined}
          actions={board && <AutonomyControl companyId={board.sprint.company_id} canManage={canManage} />}
        />
        {board && board.sprint.company_id !== companyId && (
          <div role="alert" className="alert-error items-center p-3">
            {t('workspace.otherCompany', { defaultValue: 'This sprint belongs to another company. This view is read-only.' })}
            {' '}
            <Link to="/companies/select" className="underline">{t('workspace.selectCompany', { defaultValue: 'Select company' })}</Link>
          </div>
        )}
        {boardQuery.isError && (
          <div role="alert" className="alert-error items-center p-3">
            {terminalMessage ?? t('workspace.refreshFailed', { defaultValue: 'Failed to refresh this sprint. Showing the last loaded view.' })}
            {!noAccess && !notFound && (
              <button type="button" className="btn-tool ml-auto" onClick={() => boardQuery.refetch()}>
                <RefreshCw size={13} aria-hidden="true" />{t('common.retry', { defaultValue: 'Retry' })}
              </button>
            )}
          </div>
        )}
        {board && <SprintTeamChips team={board.sprint.team} source={board.sprint.team_source} />}
        {board && <SprintStatusBanner sprint={board.sprint} />}
        <div className="flex flex-wrap items-end gap-2 border-b border-surface-700">
          <SprintTabs sprintId={sprintId} selected={tab} />
          {tab === 'board' && board && (
            <div className="ml-auto pb-1">
              <SprintControls sprint={board.sprint} />
            </div>
          )}
        </div>
        <div id="sprint-panel" role="tabpanel" aria-labelledby={`sprint-tab-${tab}`} className="min-w-0">
          {!board ? (
            <div role="status" aria-label={t('common.loading', { defaultValue: 'Loading...' })} className="space-y-2">
              {[0, 1, 2].map(index => <div key={index} className="skeleton h-14 rounded-xl motion-reduce:animate-none" />)}
            </div>
          ) : tab === 'board' ? (
            <BoardPanel sprintId={sprintId} companyId={board?.sprint.company_id ?? null} />
          ) : tab === 'live' ? (
            <LivePanel sprintId={sprintId} />
          ) : tab === 'conversation' ? (
            <Conversation companyId={board.sprint.company_id} sprintId={sprintId} live={board.sprint.status === 'active'} />
          ) : tab === 'notes' ? (
            <BoardNotes sprintId={sprintId} canManage={canManage && board.sprint.company_id === companyId}
              live={board.sprint.status === 'active'} />
          ) : tab === 'analytics' ? (
            <AnalyticsDashboard sprintId={sprintId} />
          ) : (
            <div className="space-y-4">
              <SprintOverview sprintId={sprintId} />
              <SprintProgress sprintId={sprintId} live={board.sprint.status === 'active'}
                specificationId={board.sprint.specification_id} />
              <SprintIntegration sprintId={sprintId} companyId={board.sprint.company_id}
                canReview={canManage && board.sprint.company_id === companyId}
                canConfigure={role === 'Owner' && board.sprint.company_id === companyId} />
              <SprintRelations sprintId={sprintId} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
