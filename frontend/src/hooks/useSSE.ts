import { useState, useEffect, useCallback, useRef } from 'react'

export function useSSE(url: string, onMessage?: (data: any) => void, onError?: (error: any) => void) {
  const [isConnected, setIsConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)

  const connect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
    }

    const eventSource = new EventSource(url)
    eventSourceRef.current = eventSource

    eventSource.onopen = () => {
      setIsConnected(true)
      setError(null)
    }

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.ping) return
        onMessage?.(data)
      } catch (e) {
        console.error('Failed to parse SSE message:', e)
      }
    }

    eventSource.onerror = (err) => {
      setIsConnected(false)
      setError('Connection lost')
      onError?.(err)
      eventSource.close()
      setTimeout(connect, 3000)
    }
  }, [url, onMessage, onError])

  useEffect(() => {
    connect()
    return () => {
      eventSourceRef.current?.close()
    }
  }, [connect])

  return { isConnected, error }
}
