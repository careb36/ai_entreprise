import { useCallback, useEffect, useRef, useState, type Dispatch } from 'react'
import { LiveApi, readStreamEvent, type LiveConfig } from './live'
import type { Action } from './model'

export type LiveStatus = 'demo' | 'loading' | 'live' | 'error'

/** How often the board and the runs are read again while the stream carries the events. */
export const RELOAD_MS = 30_000
/** How long the room waits before reopening a stream that dropped (its ticket lives five minutes). */
export const STREAM_RETRY_MS = 5_000

/**
 * Keeps the room in step with a backend when live mode is on: loads the
 * company, follows the sprint's Glassroom stream (each event once, a fresh
 * ticket after a drop), reads the board and the runs again now and then
 * without touching what the stream built, and records a person's decisions
 * through the API before the room shows them. In demo mode every action
 * goes straight to the reducer.
 */
export function useLiveWorld(config: LiveConfig | null, dispatch: Dispatch<Action>): { perform: (action: Action) => void; status: LiveStatus } {
  const [status, setStatus] = useState<LiveStatus>(config ? 'loading' : 'demo')
  const apiRef = useRef<LiveApi | null>(null)

  useEffect(() => {
    if (!config) return
    const api = new LiveApi(config)
    apiRef.current = api
    let stream: EventSource | null = null
    let retry: ReturnType<typeof setTimeout> | null = null
    let timer: ReturnType<typeof setInterval> | null = null
    let stopped = false
    const seen = new Set<string>()

    const load = async (reload = false) => {
      const { world, sprintId } = await api.load(reload)
      if (stopped) return
      dispatch({ type: 'load', world })
      setStatus('live')
      return sprintId
    }

    const follow = async (sprintId: number) => {
      if (typeof EventSource === 'undefined') return
      const url = await api.streamUrl(sprintId)
      if (stopped) return
      stream = new EventSource(url)
      stream.onmessage = message => {
        try {
          const event = JSON.parse(message.data)
          if (typeof event.event_id === 'string') {
            if (seen.has(event.event_id)) return
            seen.add(event.event_id)
          }
          const { log, agent } = readStreamEvent(event)
          if (log) dispatch({ type: 'event', text: log.text, at: log.at })
          if (agent) dispatch({ type: 'agent', agent })
        } catch {
          // A line the room cannot read is not an event.
        }
      }
      stream.onerror = () => {
        // The ticket may have expired: open a new stream with a new one.
        stream?.close()
        stream = null
        if (stopped) return
        retry = setTimeout(() => void follow(sprintId), STREAM_RETRY_MS)
      }
    }

    load()
      .then(sprintId => {
        if (stopped) return
        if (sprintId !== undefined) void follow(sprintId)
        timer = setInterval(() => void load(true).catch(() => setStatus('error')), RELOAD_MS)
      })
      .catch(() => {
        if (!stopped) setStatus('error')
      })

    return () => {
      stopped = true
      stream?.close()
      if (retry) clearTimeout(retry)
      if (timer) clearInterval(timer)
      apiRef.current = null
    }
  }, [config, dispatch])

  const perform = useCallback(
    (action: Action) => {
      const api = apiRef.current
      if (!api || (action.type !== 'decide_run' && action.type !== 'send_back')) {
        dispatch(action)
        return
      }
      // The backend records the decision; the room shows it once it did, and
      // says so if the backend refused it.
      const request = action.type === 'decide_run' ? api.decideRun(action.id, action.decision) : api.sendBack(action.id)
      request.then(
        () => dispatch(action),
        (error: unknown) => {
          dispatch({ type: 'event', text: `The backend did not record that: ${error instanceof Error ? error.message : String(error)}` })
        },
      )
    },
    [dispatch],
  )

  return { perform, status }
}
