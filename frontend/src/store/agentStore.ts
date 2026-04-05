import { create } from 'zustand'

export interface AgentMessage {
  id: string
  timestamp: string
  agent: 'PO' | 'ScrumMaster' | 'Dev' | 'System'
  content: string
  type: 'message' | 'action' | 'status' | 'error'
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
    const { disconnect } = get()
    disconnect()
    
    set({ sprintId, connectionError: null })
    
    const eventSource = new EventSource(`${BACKEND_URL}/v1/glassroom/stream?sprint_id=${sprintId}`)
    
    eventSource.onopen = () => {
      set({ isConnected: true, connectionError: null })
    }
    
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.ping) return
        
        const message: AgentMessage = {
          id: data.id || crypto.randomUUID(),
          timestamp: data.timestamp || new Date().toISOString(),
          agent: data.agent || 'System',
          content: data.content || JSON.stringify(data),
          type: data.type || 'message'
        }
        
        get().addMessage(message)
      } catch (e) {
        console.error('Failed to parse SSE message:', e)
      }
    }
    
    eventSource.onerror = (err) => {
      set({ isConnected: false, connectionError: 'Connection lost' })
      eventSource.close()
    }
    
    ;(window as any).__eventSource = eventSource
  },

  disconnect: () => {
    const eventSource = (window as any).__eventSource
    if (eventSource) {
      eventSource.close()
      delete (window as any).__eventSource
    }
    set({ isConnected: false, sprintId: null })
  }
}))
