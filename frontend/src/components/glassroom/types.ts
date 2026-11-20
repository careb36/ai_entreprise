import type { AgentMessage } from '../../store/agentStore'
import type { BacklogItem } from '../../services/api'
import type { AgentIdentity } from './identity'

export interface MessageGroup {
  agent: string
  identity: AgentIdentity
  timestamp: string
  messages: AgentMessage[]
}

export interface HealthPanelEntry {
  label: string
  value: string
  tone: 'neutral' | 'good' | 'warn' | 'danger'
}

export interface QuotaTelemetry {
  quota: number
  used: number
  remaining: number
  ratio: number
}

export interface SprintTaskEntry {
  id: number
  title: string
  status: BacklogItem['status']
  priority: BacklogItem['priority']
  assignee?: string
  comments?: BacklogItem['comments']
}

export interface TaskAutonomyEntry {
  id: number
  title: string
  status: BacklogItem['status']
  priority: BacklogItem['priority']
  assignee?: string
  currentPhase: string
  reworkCount: number
  reviewCount: number
  deliverableCount: number
  blockedReason?: string
  stepStates: Array<{
    key: string
    label: string
    state: 'pending' | 'active' | 'done' | 'blocked'
    detail?: string
  }>
}
