import { create } from 'zustand'
import {
  type AgentEvent,
  type AgentFsmState,
  normalizeSSEEvent,
  assignRolePalette,
} from '../lib/agentEvents'

export interface AgentMessage {
  id: string
  timestamp: string
  agent: string
  role?: string
  task?: string
  preview?: string
  content: string
  type: 'message' | 'action' | 'status' | 'error' | 'agent_start' | 'agent_complete'
}

/** Per-agent live state derived from normalized AgentEvents (pixel-agents FSM pattern). */
export interface AgentLiveState {
  role: string
  fsmState: AgentFsmState
  currentActivity: string  // last formatted status from nodeStart
  color: string            // Tailwind color token (from assignRolePalette)
  lastSeen: string         // ISO timestamp
}

interface AgentStore {
  messages: AgentMessage[]
  agentStates: Record<string, AgentLiveState>   // keyed by agentRole
  rolePalettes: Record<string, string>           // role → Tailwind color token
  isConnected: boolean
  connectionError: string | null
  sprintId: string | null

  addMessage: (message: AgentMessage) => void
  applyAgentEvent: (event: AgentEvent) => void
  setConnected: (connected: boolean) => void
  setConnectionError: (error: string | null) => void
  setSprintId: (sprintId: string | null) => void
  clearMessages: () => void
  connect: (sprintId: string) => void
  disconnect: () => void
}

const BACKEND_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const RECONNECT_DELAY_MS = 3_000
const MAX_RECONNECT_ATTEMPTS = 10
const MAX_RECONNECT_DELAY_MS = 30_000

let activeEventSource: EventSource | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectAttempt = 0
let connectionToken = 0

function clearReconnectTimer() {
  if (!reconnectTimer) return
  clearTimeout(reconnectTimer)
  reconnectTimer = null
}

function closeActiveEventSource() {
  if (!activeEventSource) return
  activeEventSource.close()
  activeEventSource = null
}

export const useAgentStore = create<AgentStore>((set, get) => {
  const openConnection = (sprintId: string, token: number) => {
    clearReconnectTimer()
    closeActiveEventSource()

    const eventSource = new EventSource(
      `${BACKEND_URL}/v1/glassroom/stream?sprint_id=${encodeURIComponent(sprintId)}`,
    )
    activeEventSource = eventSource

    eventSource.onopen = () => {
      if (token !== connectionToken || activeEventSource !== eventSource) {
        eventSource.close()
        return
      }
      reconnectAttempt = 0
      set({ isConnected: true, connectionError: null, sprintId })
    }

    eventSource.onmessage = (event) => {
      if (token !== connectionToken || activeEventSource !== eventSource) return

      try {
        const raw = JSON.parse(event.data) as Record<string, unknown>
        if (raw.ping) return

        const normalized = normalizeSSEEvent(raw)
        if (normalized) {
          get().applyAgentEvent(normalized)

          if (normalized.kind === 'nodeStart') {
            get().addMessage({
              id: String(raw.id || crypto.randomUUID()),
              timestamp: normalized.timestamp,
              agent: normalized.agentRole || 'System',
              role: normalized.agentRole || 'System',
              task: normalized.nodeName,
              content: normalized.status,
              type: 'agent_start',
            })
          } else if (normalized.kind === 'nodeEnd') {
            get().addMessage({
              id: String(raw.id || crypto.randomUUID()),
              timestamp: normalized.timestamp,
              agent: normalized.agentRole || 'System',
              role: normalized.agentRole || 'System',
              task: normalized.nodeName,
              content: `${normalized.agentRole || 'System'} finished ${normalized.nodeName}`,
              type: 'agent_complete',
            })
          } else if (normalized.kind === 'agentMessage') {
            get().addMessage({
              id: String(raw.id || crypto.randomUUID()),
              timestamp: normalized.timestamp,
              agent: normalized.agentRole || 'System',
              role: normalized.agentRole || 'System',
              task: typeof raw.task === 'string' ? raw.task : undefined,
              preview: typeof raw.preview === 'string' ? raw.preview : undefined,
              content: normalized.content,
              type: 'message',
            })
          } else if (normalized.kind === 'nodeError') {
            get().addMessage({
              id: String(raw.id || crypto.randomUUID()),
              timestamp: normalized.timestamp,
              agent: normalized.agentRole || 'System',
              role: normalized.agentRole || 'System',
              task: normalized.nodeName,
              content: `Error in ${normalized.nodeName}: ${normalized.error}`,
              type: 'error',
            })
          } else if (normalized.kind === 'system') {
            get().addMessage({
              id: crypto.randomUUID(),
              timestamp: normalized.timestamp,
              agent: 'System',
              content: normalized.content,
              type: 'status',
            })
          }
          return
        }

        get().addMessage({
          id: String(raw.id || crypto.randomUUID()),
          timestamp: String(raw.timestamp || new Date().toISOString()),
          agent: String(raw.agent || 'System'),
          role: typeof raw.role === 'string' ? raw.role : undefined,
          task: typeof raw.task === 'string' ? raw.task : undefined,
          preview: typeof raw.preview === 'string' ? raw.preview : undefined,
          content: String(raw.content || JSON.stringify(raw)),
          type:
            raw.type === 'agent_start' ||
            raw.type === 'agent_complete' ||
            raw.type === 'action' ||
            raw.type === 'status' ||
            raw.type === 'error' ||
            raw.type === 'message'
              ? raw.type
              : 'message',
        })
      } catch (error) {
        console.error('Failed to parse SSE message:', error)
      }
    }

    eventSource.onerror = () => {
      if (token !== connectionToken || activeEventSource !== eventSource) {
        eventSource.close()
        return
      }

      closeActiveEventSource()
      set({ isConnected: false })

      if (reconnectAttempt >= MAX_RECONNECT_ATTEMPTS) {
        set({
          connectionError: `Could not reconnect after ${MAX_RECONNECT_ATTEMPTS} attempts.`,
        })
        return
      }

      const delay = Math.min(
        RECONNECT_DELAY_MS * 2 ** reconnectAttempt,
        MAX_RECONNECT_DELAY_MS,
      )
      reconnectAttempt += 1
      set({
        connectionError: `Connection lost. Reconnecting in ${Math.round(delay / 1000)}s...`,
      })

      reconnectTimer = setTimeout(() => {
        if (token !== connectionToken) return
        openConnection(sprintId, token)
      }, delay)
    }
  }

  return {
    messages: [],
    agentStates: {},
    rolePalettes: {},
    isConnected: false,
    connectionError: null,
    sprintId: null,

    addMessage: (message) =>
      set((state) => ({ messages: [...state.messages, message] })),

    /**
     * Drive per-agent FSM state from a normalized AgentEvent.
     * Adapted from pixel-agents' hookEventHandler dispatch (markAgentWaiting,
     * handlePreToolUse, handleStop, etc.) — collapsed to a Zustand reducer.
     */
    applyAgentEvent: (event: AgentEvent) => {
      set((state) => {
        const palettes = { ...state.rolePalettes }
        const agentStates = { ...state.agentStates }

        const upsertRole = (role: string, patch: Partial<AgentLiveState>) => {
          const color = assignRolePalette(palettes, role)
          palettes[role] = color
          const defaults: AgentLiveState = {
            role,
            fsmState: 'idle',
            currentActivity: '',
            color,
            lastSeen: new Date().toISOString(),
          }
          agentStates[role] = Object.assign(
            defaults,
            agentStates[role] ?? {},
            patch,
            { lastSeen: event.timestamp || new Date().toISOString() },
          )
        }

        switch (event.kind) {
          case 'nodeStart':
            upsertRole(event.agentRole, {
              fsmState: 'active',
              currentActivity: event.status,
            })
            break
          case 'nodeEnd':
            upsertRole(event.agentRole, {
              fsmState: 'waiting',
              currentActivity: '',
            })
            break
          case 'nodeError':
            upsertRole(event.agentRole, {
              fsmState: 'error',
              currentActivity: event.error,
            })
            break
          case 'agentMessage':
            upsertRole(event.agentRole, {
              fsmState: 'active',
              currentActivity: event.content.slice(0, 60),
            })
            break
          // 'system' events don't map to a specific role
        }

        return { agentStates, rolePalettes: palettes }
      })
    },

    setConnected: (connected) => set({ isConnected: connected }),
    setConnectionError: (error) => set({ connectionError: error }),
    setSprintId: (sprintId) => set({ sprintId }),
    clearMessages: () => set({ messages: [], agentStates: {}, rolePalettes: {} }),

    connect: (sprintId) => {
      const token = ++connectionToken
      get().clearMessages()
      reconnectAttempt = 0
      clearReconnectTimer()
      closeActiveEventSource()
      set({ isConnected: false, sprintId, connectionError: null })
      openConnection(sprintId, token)
    },

    disconnect: () => {
      connectionToken += 1
      reconnectAttempt = 0
      clearReconnectTimer()
      closeActiveEventSource()
      set({ isConnected: false, sprintId: null, connectionError: null })
    },
  }
})
