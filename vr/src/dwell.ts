/**
 * Gaze dwell: an eyes-only alternative to the pinch. Looking at a card for
 * `DWELL_MS` picks it, once; looking away resets. No rendering here; the
 * scene feeds it what the gaze hits each frame and draws the progress.
 */

/** How long a card must be looked at before it counts as picked. */
export const DWELL_MS = 1_500

export interface DwellStep {
  /** 0 to 1 while a target is held; 0 with none. */
  progress: number
  /** True on the one frame the dwell completes. */
  fire: boolean
}

export class Dwell {
  private target: string | null = null
  private since = 0
  private fired = false

  constructor(private readonly ms = DWELL_MS) {}

  /** The target under the gaze at `now` (ms), or null for none. */
  update(target: string | null, now: number): DwellStep {
    if (target !== this.target) {
      this.target = target
      this.since = now
      this.fired = false
    }
    if (target === null) return { progress: 0, fire: false }
    const progress = Math.min(1, (now - this.since) / this.ms)
    if (progress >= 1 && !this.fired) {
      this.fired = true
      return { progress: 1, fire: true }
    }
    return { progress, fire: false }
  }
}
