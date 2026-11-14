import { useMemo } from 'react'
import type { TFunction } from 'i18next'
import type { AgentLiveState, AgentMessage, SprintRetro } from '../../store/agentStore'
import type { BacklogItem, SprintBoard } from '../../services/api'
import { extractBlockedReason, formatAgeShort, hasDeliverableComment, parseCooldownUntil, phaseLabel } from './format'
import type {
  AssistantSummaryEntry, HealthPanelEntry, QuotaTelemetry, SprintTaskEntry, TaskAutonomyEntry,
} from './types'

/** Per console assistant: how often it was used, skipped for quota, failed or requested. */
export function useAssistantSummary(messages: AgentMessage[]) {
  return useMemo<AssistantSummaryEntry[]>(() => {
    const summary = new Map<string, AssistantSummaryEntry>()
    for (const msg of messages) {
      if (!msg.assistantLabel || !msg.assistantDecision) continue
      const current = summary.get(msg.assistantLabel) ?? {
        label: msg.assistantLabel,
        used: 0,
        quotaSkips: 0,
        failures: 0,
        unavailableSkips: 0,
        requested: 0,
        cooldownUntil: undefined,
        lastDecision: undefined,
      }

      if (msg.assistantDecision === 'used') {
        current.used += 1
      } else if (msg.assistantDecision === 'quota_cooldown' || msg.assistantDecision === 'skipped_quota_cooldown') {
        current.quotaSkips += 1
      } else if (msg.assistantDecision === 'failed' || msg.assistantDecision === 'unavailable') {
        current.failures += 1
      } else if (msg.assistantDecision === 'requested') {
        current.requested += 1
      } else if (msg.assistantDecision === 'unavailable_cooldown' || msg.assistantDecision === 'skipped_unavailable_cooldown') {
        current.unavailableSkips += 1
      }

      current.lastDecision = msg.assistantDecision
      current.cooldownUntil = parseCooldownUntil(msg.assistantDetail) || current.cooldownUntil

      summary.set(msg.assistantLabel, current)
    }

    return [...summary.values()].sort((left, right) => left.label.localeCompare(right.label))
  }, [messages])
}

/** The sprint's tasks, in progress first. */
export function useSprintTasks(board: SprintBoard | undefined) {
  return useMemo<SprintTaskEntry[]>(() => {
    if (!board?.columns) return []

    const order: Array<BacklogItem['status']> = ['in_progress', 'in_review', 'blocked', 'todo', 'done']
    const entries = Object.values(board.columns)
      .flat()
      .map((task) => ({
        id: task.id,
        title: task.title,
        status: task.status,
        priority: task.priority,
        assignee: task.assignee,
        comments: task.comments,
      }))

    return entries.sort((left, right) => {
      const statusDelta = order.indexOf(left.status) - order.indexOf(right.status)
      if (statusDelta !== 0) return statusDelta
      return left.id - right.id
    })
  }, [board])
}

/** The company's token quota summed over its rows; undefined when there is none. */
export function useQuotaTelemetry(quotas: unknown) {
  return useMemo<QuotaTelemetry | undefined>(() => {
    const rows = Array.isArray(quotas) ? quotas : []
    const quota = rows.reduce((total, row) => total + Number(row?.limit ?? row?.quota ?? 0), 0)
    const used = rows.reduce((total, row) => total + Number(row?.used ?? row?.token_used ?? 0), 0)
    if (!Number.isFinite(quota) || quota <= 0 || !Number.isFinite(used)) return undefined
    return {
      quota,
      used,
      remaining: Math.max(0, quota - used),
      ratio: Math.max(0, Math.min(1, used / quota)),
    }
  }, [quotas])
}

/** The operational health tiles of the right panel. */
export function useHealthEntries({ agentStates, clockTick, isConnected, messages, sprintTasks, memoryHits, lastRetro, t }: {
  agentStates: Record<string, AgentLiveState>
  clockTick: number
  isConnected: boolean
  messages: AgentMessage[]
  sprintTasks: SprintTaskEntry[]
  memoryHits: number
  lastRetro: SprintRetro | null
  t: TFunction
}) {
  return useMemo<HealthPanelEntry[]>(() => {
    const now = clockTick
    const liveRoles = Object.values(agentStates)
    const activeCount = liveRoles.filter(entry => entry.fsmState === 'active').length
    const errorCount = liveRoles.filter(entry => entry.fsmState === 'error').length
    const staleMs = liveRoles.length > 0
      ? Math.min(...liveRoles.map(entry => Math.max(0, now - Date.parse(entry.lastSeen || ''))))
      : 0
    const blockedCount = sprintTasks.filter(task => task.status === 'blocked').length
    const reworkOpen = messages.filter(msg => msg.semanticType === 'task.rework_scheduled').length

    const retroRate = lastRetro?.completionRate ?? null
    const retroTone: HealthPanelEntry['tone'] = retroRate === null ? 'neutral' : retroRate >= 80 ? 'good' : retroRate >= 50 ? 'warn' : 'danger'

    return [
      { label: t('glassroom.healthLabels.stream'), value: isConnected ? t('glassroom.streamStatus.live', 'Live') : t('glassroom.streamStatus.offline', 'Offline'), tone: isConnected ? 'good' : 'danger' },
      { label: t('glassroom.healthLabels.activeAgents'), value: String(activeCount), tone: activeCount > 0 ? 'good' : 'warn' },
      { label: t('glassroom.healthLabels.agentErrors'), value: String(errorCount), tone: errorCount > 0 ? 'danger' : 'good' },
      { label: t('glassroom.healthLabels.blockedTasks'), value: String(blockedCount), tone: blockedCount > 0 ? 'warn' : 'good' },
      { label: t('glassroom.healthLabels.openReworks'), value: String(reworkOpen), tone: reworkOpen > 0 ? 'warn' : 'neutral' },
      { label: t('glassroom.healthLabels.freshestHeartbeat'), value: liveRoles.length > 0 ? t('glassroom.agoWithValue', '{{value}} ago').replace('{{value}}', formatAgeShort(staleMs)) : t('glassroom.notAvailable', 'n/a'), tone: staleMs <= 30000 ? 'good' : 'warn' },
      { label: t('glassroom.healthLabels.memoryHits', 'Memory hits'), value: String(memoryHits), tone: memoryHits > 0 ? 'good' : 'neutral' },
      ...(retroRate !== null ? [{ label: t('glassroom.healthLabels.lastSprintCompletion', 'Last sprint'), value: `${retroRate}%`, tone: retroTone }] : []),
    ]
  }, [agentStates, clockTick, isConnected, messages, sprintTasks, memoryHits, lastRetro, t])
}

/** Each task's path through queue, execution, review, rework and done. */
export function useAutonomyTree(messages: AgentMessage[], sprintTasks: SprintTaskEntry[], t: TFunction) {
  return useMemo<TaskAutonomyEntry[]>(() => {
    return sprintTasks.map(task => {
      const relatedMessages = messages.filter(msg => msg.taskId === task.id || msg.task === task.title)
      const reworkCount = relatedMessages.reduce((count, msg) => Math.max(count, msg.reworkCount ?? 0), 0)
      const reviewCount = (task.comments || []).filter(comment => comment.author === 'QA').length
      const deliverableCount = (task.comments || []).filter(hasDeliverableComment).length
      const currentPhase = [...relatedMessages].reverse().find(msg => msg.taskPhase)?.taskPhase || 'unknown'
      const blockedReason = task.status === 'blocked' ? extractBlockedReason(task.comments) : undefined
      const assistantLabels = [...new Set(relatedMessages.map(msg => msg.assistantLabel).filter(Boolean) as string[])]

      const stepStates: TaskAutonomyEntry['stepStates'] = [
        {
          key: 'queued',
          label: t('glassroom.treeSteps.queued'),
          state: relatedMessages.some(msg => msg.semanticType === 'task.queued') ? 'done' : task.status === 'todo' ? 'active' : 'pending',
        },
        {
          key: 'execution',
          label: t('glassroom.treeSteps.execution'),
          state: task.status === 'in_progress' ? 'active' : deliverableCount > 0 || ['in_review', 'done', 'blocked'].includes(task.status) ? 'done' : 'pending',
          detail: currentPhase !== 'unknown' ? phaseLabel(t, currentPhase) : undefined,
        },
        {
          key: 'review',
          label: t('glassroom.treeSteps.review'),
          state: task.status === 'in_review' ? 'active' : reviewCount > 0 || task.status === 'done' ? 'done' : task.status === 'blocked' && deliverableCount > 0 ? 'blocked' : 'pending',
          detail: reviewCount > 0 ? t('glassroom.reviewsCount', '{{n}} reviews').replace('{{n}}', String(reviewCount)) : undefined,
        },
        {
          key: 'rework',
          label: t('glassroom.treeSteps.rework'),
          state: reworkCount > 0 ? (task.status === 'blocked' ? 'blocked' : task.status === 'todo' ? 'active' : 'done') : 'pending',
          detail: reworkCount > 0 ? t('glassroom.cyclesCount', '{{n}} cycle(s)').replace('{{n}}', String(reworkCount)) : undefined,
        },
        {
          key: 'done',
          label: t('glassroom.treeSteps.done'),
          state: task.status === 'done' ? 'done' : task.status === 'blocked' ? 'blocked' : 'pending',
          detail: blockedReason,
        },
      ]

      return {
        id: task.id,
        title: task.title,
        status: task.status,
        priority: task.priority,
        assignee: task.assignee,
        currentPhase,
        reworkCount,
        reviewCount,
        deliverableCount,
        blockedReason,
        assistantLabels,
        stepStates,
      }
    }).sort((left, right) => {
      if (left.status === right.status) return left.id - right.id
      const order = ['in_progress', 'in_review', 'blocked', 'todo', 'done']
      return order.indexOf(left.status) - order.indexOf(right.status)
    })
  }, [messages, sprintTasks, t])
}
