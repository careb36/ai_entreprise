import { describe, expect, it } from 'vitest'
import { DWELL_MS, Dwell } from './dwell'

describe('gaze dwell', () => {
  it('picks a card looked at long enough, once, and resets when the gaze moves', () => {
    const dwell = new Dwell()
    expect(dwell.update(null, 0)).toEqual({ progress: 0, fire: false })
    expect(dwell.update('card-1', 100)).toEqual({ progress: 0, fire: false })
    expect(dwell.update('card-1', 100 + DWELL_MS / 2)).toEqual({ progress: 0.5, fire: false })
    expect(dwell.update('card-1', 100 + DWELL_MS)).toEqual({ progress: 1, fire: true })
    // Held longer: picked already, nothing fires again.
    expect(dwell.update('card-1', 100 + DWELL_MS * 3)).toEqual({ progress: 1, fire: false })
    // Away and back: the count starts over.
    expect(dwell.update(null, 5_000)).toEqual({ progress: 0, fire: false })
    expect(dwell.update('card-1', 5_100)).toEqual({ progress: 0, fire: false })
    expect(dwell.update('card-1', 5_100 + DWELL_MS)).toEqual({ progress: 1, fire: true })
  })

  it('switching cards restarts the count on the new one', () => {
    const dwell = new Dwell(1_000)
    dwell.update('a', 0)
    expect(dwell.update('a', 900).progress).toBeCloseTo(0.9)
    expect(dwell.update('b', 1_000)).toEqual({ progress: 0, fire: false })
    expect(dwell.update('b', 1_999).fire).toBe(false)
    expect(dwell.update('b', 2_000).fire).toBe(true)
  })
})
