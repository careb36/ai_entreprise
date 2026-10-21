import { describe, expect, it } from 'vitest'
import { parseFlags } from './flags'

describe('the page flags', () => {
  it('reads stats and gaze from the URL, off by default', () => {
    expect(parseFlags('')).toEqual({ stats: false, gaze: false })
    expect(parseFlags('?stats=1')).toEqual({ stats: true, gaze: false })
    expect(parseFlags('?api=https://x&company=4&gaze=1&stats=0')).toEqual({ stats: false, gaze: true })
  })
})
