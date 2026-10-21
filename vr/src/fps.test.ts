import { describe, expect, it } from 'vitest'
import { FrameRate, statsWanted } from './fps'

describe('the frame rate for the Quest pass', () => {
  it('reports the rate once per window, from the frames counted in it', () => {
    const rate = new FrameRate(500)
    expect(rate.tick(0)).toBeNull()
    const ticks: (number | null)[] = []
    for (let frame = 1; frame <= 50; frame += 1) ticks.push(rate.tick(frame * 10))
    // Fifty frames, one every 10 ms, cross the 500 ms window on the last one.
    expect(ticks.slice(0, -1).every(tick => tick === null)).toBe(true)
    expect(ticks.at(-1)).toBe(100)
    // The next window starts from there: twenty-five frames in 500 ms is 50 fps.
    let last: number | null = null
    for (let frame = 1; frame <= 25; frame += 1) last = rate.tick(500 + frame * 20)
    expect(last).toBe(50)
  })

  it('is shown only with ?stats=1', () => {
    expect(statsWanted('?stats=1')).toBe(true)
    expect(statsWanted('?api=https://x&stats=1')).toBe(true)
    expect(statsWanted('?stats=0')).toBe(false)
    expect(statsWanted('')).toBe(false)
  })
})
