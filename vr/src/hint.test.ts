import { describe, expect, it } from 'vitest'
import { HINT_MS, approach, hintTarget, hintVisible } from './hint'

describe('the hint on entering the room', () => {
  it('shows for the first seconds of a session and goes with the first pinch', () => {
    expect(hintVisible({ enteredAt: null, pinched: false }, 1_000)).toBe(false)
    expect(hintVisible({ enteredAt: 1_000, pinched: false }, 1_000)).toBe(true)
    expect(hintVisible({ enteredAt: 1_000, pinched: false }, 1_000 + HINT_MS - 1)).toBe(true)
    expect(hintVisible({ enteredAt: 1_000, pinched: false }, 1_000 + HINT_MS)).toBe(false)
    expect(hintVisible({ enteredAt: 1_000, pinched: true }, 1_500)).toBe(false)
  })

  it('sits in front of the head, a little below the eyes, along the flattened gaze', () => {
    expect(hintTarget([0, 1.2, 0], [0, 0, -1])).toEqual([0, 1.08, -0.9])
    const [x, y, z] = hintTarget([0, 1.2, 0], [1, 0, 0], 0.5, 0.1)
    expect(x).toBeCloseTo(0.5)
    expect(y).toBeCloseTo(1.1)
    expect(z).toBeCloseTo(0)
    // Looking at the floor still puts it ahead, not under the chair.
    const down = hintTarget([0, 1.2, 0], [0, -1, 0])
    expect(down[1]).toBeCloseTo(1.08)
    expect(down[2]).toBeCloseTo(-0.9)
    // A tilted gaze keeps the distance along the floor.
    const tilted = hintTarget([0, 1.2, 0], [0, -0.5, -0.5])
    expect(Math.hypot(tilted[0], tilted[2])).toBeCloseTo(0.9)
  })

  it('approaches the target a step at a time, never past it', () => {
    expect(approach([0, 0, 0], [1, 2, 3], 0.5)).toEqual([0.5, 1, 1.5])
    expect(approach([0, 0, 0], [1, 2, 3], 1)).toEqual([1, 2, 3])
    expect(approach([0, 0, 0], [1, 2, 3], 2)).toEqual([1, 2, 3])
    expect(approach([1, 1, 1], [1, 1, 1], 0.1)).toEqual([1, 1, 1])
  })
})
