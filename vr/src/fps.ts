/**
 * Frames per second, counted over a window, for the Quest pass: the
 * competition asks for 60 fps on the headset, and the browser's own
 * counters are not visible from inside a session. Shown in the room with
 * `?stats=1`.
 */
export class FrameRate {
  private frames = 0
  private since: number | null = null

  constructor(private readonly windowMs = 500) {}

  /** Counts a frame at `now` (ms); returns the rate once a window has elapsed, else null. */
  tick(now: number): number | null {
    if (this.since === null) {
      this.since = now
      this.frames = 0
      return null
    }
    this.frames += 1
    const elapsed = now - this.since
    if (elapsed < this.windowMs) return null
    const rate = Math.round((this.frames * 1000) / elapsed)
    this.since = now
    this.frames = 0
    return rate
  }
}

/** `?stats=1` shows the frame rate in the room. */
export function statsWanted(search: string): boolean {
  return new URLSearchParams(search).get('stats') === '1'
}
