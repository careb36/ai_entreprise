import { useEffect, type Dispatch } from 'react'
import { demoTimeline } from './demo'
import type { Action } from './model'

/** Plays the demo's first minute while the room shows the demo company; nothing in live mode. */
export function useDemoTimeline(enabled: boolean, dispatch: Dispatch<Action>): void {
  useEffect(() => {
    if (!enabled) return
    const timers = demoTimeline().map(step =>
      setTimeout(() => {
        for (const action of step.actions) dispatch(action)
      }, step.at),
    )
    return () => {
      for (const timer of timers) clearTimeout(timer)
    }
  }, [enabled, dispatch])
}
