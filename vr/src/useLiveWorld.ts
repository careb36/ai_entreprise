import { useCallback, useEffect, useRef, useState, type Dispatch } from 'react'
import { LiveApi, readStreamEvent, type LiveConfig } from './live'
import type { Action } from './model'

export type LiveStatus = 'demo' | 'loading' | 'live' | 'error'

/** How often the board and the runs are read again while the stream carries the events. */
export const RELOAD_MS = 30_000

/**
 * Keeps the room in step with a backend when live mode is on: loads the
 * company, follows the sprint's Glassroom stream, reads the board again now
 * and then, and records a person's decisions through the API before the
 * room shows them. In demo mode every action goes straight to the reducer.
 */
export function useLiveWorld(config: LiveConfig | null, dispatch: Dispatch<Action>): { perform: (action: Action) => void; status: LiveStatus } {
  const [status, setStatus] = useState<LiveStatus>(config ? 'loading' : 'demo')
  const apiRef = useRef<LiveApi | null>(null)

  useEffect(() => {
    if (!config) return
    const api = new LiveApi(config)
    apiRef.current = api
    let stream: EventSource | null = null
    let timer: ReturnType<typeof setInterval> | null = null
    let stopped = false

    const load = async () => {
      const { world, sprintId } = await api.load()
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
          const { log, agent } = readStreamEvent(JSON.parse(message.data))
          if (log) dispatch({ type: 'event', text: log.text, at: log.at })
          if (agent) dispatch({ type: 'agent', agent })
        } catch {
          // A line the room cannot read is not an event.
        }
      }
    }

    load()
      .then(sprintId => {
        if (sprintId !== undefined) void follow(sprintId)
        timer = setInterval(() => void load().catch(() => setStatus('error')), RELOAD_MS)
      })
      .catch(() => {
        if (!stopped) setStatus('error')
      })

    return () => {
      stopped = true
      stream?.close()
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
      // The backend records the decision; the room shows it as soon as it is sent
      // and says so if the backend refused it.
      dispatch(action)
      const request = action.type === 'decide_run' ? api.decideRun(action.id, action.decision) : api.sendBack(action.id)
      request.catch((error: unknown) => {
        dispatch({ type: 'event', text: `The backend did not record that: ${error instanceof Error ? error.message : String(error)}` })
      })
    },
    [dispatch],
  )

  return { perform, status }
}
