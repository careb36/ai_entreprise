import { create } from 'zustand'

export interface AgentMessage {
  id: string
  timestamp: string
  agent: string
  content: string
  type: 'message' | 'action' | 'status' | 'error' | 'agent_start' | 'agent_complete'
  role?: string
  task?: string
  preview?: string
}

interface AgentStore {
  messages: AgentMessage[]
  isConnected: boolean
  connectionError: string | null
  sprintId: string | null
  
  addMessage: (message: AgentMessage) => void
  setConnected: (connected: boolean) => void
  setConnectionError: (error: string | null) => void
  setSprintId: (sprintId: string | null) => void
  clearMessages: () => void
  connect: (sprintId: string) => void
  disconnect: () => void
}

const BACKEND_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

// Reconnection state (kept outside Zustand to avoid re-renders)
let _reconnectTimer: ReturnType<typeof setTimeout> | null = null
let _reconnectAttempts = 0
let _activeEventSource: EventSource | null = null
let _connectionToken = 0
const MAX_RECONNECT_DELAY = 30_000   // cap at 30s
const BASE_RECONNECT_DELAY = 2_000   // start at 2s

function clearReconnectTimer() {
  if (_reconnectTimer) {
    clearTimeout(_reconnectTimer)
    _reconnectTimer = null
  }
}

function closeActiveEventSource() {
  if (_activeEventSource) {
    _activeEventSource.close()
    _activeEventSource = null
  }
}

export const useAgentStore = create<AgentStore>((set, get) => ({
  messages: [],
  isConnected: false,
  connectionError: null,
  sprintId: null,

  addMessage: (message) => {
    set((state) => ({
      messages: [...state.messages, message]
    }))
  },

  setConnected: (connected) => set({ isConnected: connected }),
  setConnectionError: (error) => set({ connectionError: error }),
  setSprintId: (sprintId) => set({ sprintId }),
  
  clearMessages: () => set({ messages: [] }),

  connect: (sprintId) => {
    const { disconnect, clearMessages } = get()
    disconnect()

    clearMessages()
    clearReconnectTimer()
    _reconnectAttempts = 0
    _connectionToken += 1

    // Clear old messages — history will be replayed by the SSE endpoint.
    set({ sprintId, connectionError: null })

    _openSSE(sprintId, _connectionToken)
  },

  disconnect: () => {
    _connectionToken += 1
    clearReconnectTimer()
    closeActiveEventSource()
    _reconnectAttempts = 0

    set({ isConnected: false, sprintId: null, connectionError: null })
  }
}))

/** Open an EventSource with auto-reconnect on failure. */
function _openSSE(sprintId: string, token: number) {
  const store = useAgentStore.getState
  clearReconnectTimer()
  closeActiveEventSource()

  const eventSource = new EventSource(`${BACKEND_URL}/v1/glassroom/stream?sprint_id=${sprintId}`)
  _activeEventSource = eventSource

  eventSource.onopen = () => {
    if (token !== _connectionToken || _activeEventSource !== eventSource) {
      eventSource.close()
      return
    }
    _reconnectAttempts = 0
    store().setConnected(true)
    store().setConnectionError(null)
  }

  eventSource.onmessage = (event) => {
    if (token !== _connectionToken || _activeEventSource !== eventSource) return

    try {
      const data = JSON.parse(event.data)
      if (data.ping || data.type === 'connected') return

      // Map backend event fields to AgentMessage.
      // Backend sends: { type, agent, role, task, message, preview, timestamp }
      const message: AgentMessage = {
        id: data.id || crypto.randomUUID(),
        timestamp: data.timestamp || new Date().toISOString(),
        agent: data.role || data.agent || 'System',
        content: data.message || data.content || JSON.stringify(data),
        type: data.type || 'message',
        role: data.role,
        task: data.task,
        preview: data.preview,
      }

      store().addMessage(message)
    } catch (e) {
      console.error('Failed to parse SSE message:', e)
    }
  }

  eventSource.onerror = () => {
    if (token !== _connectionToken || _activeEventSource !== eventSource) {
      eventSource.close()
      return
    }

    closeActiveEventSource()
    store().setConnected(false)

    // Schedule reconnect with exponential backoff
    const delay = Math.min(BASE_RECONNECT_DELAY * 2 ** _reconnectAttempts, MAX_RECONNECT_DELAY)
    _reconnectAttempts++
    store().setConnectionError(`Reconnecting in ${Math.round(delay / 1000)}s…`)

    _reconnectTimer = setTimeout(() => {
      if (token !== _connectionToken) return
      const currentSprint = store().sprintId
      if (currentSprint) {
        _openSSE(currentSprint, token)
      }
    }, delay)
  }
}
