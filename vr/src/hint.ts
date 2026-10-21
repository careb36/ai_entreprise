/**
 * The hint a person sees on entering the room in a headset, and where it
 * goes: a small panel that follows the head lazily for the first seconds,
 * until the first pinch. No rendering here; the scene reads these.
 */

/** How long the hint stays after entering, when nothing is pinched. */
export const HINT_MS = 12_000

export interface HintState {
  /** When the headset session started, or null outside one. */
  enteredAt: number | null
  /** Whether the person has pinched anything since entering. */
  pinched: boolean
}

/** The hint shows for the first seconds of a session, until the first pinch. */
export function hintVisible({ enteredAt, pinched }: HintState, now: number): boolean {
  if (enteredAt === null || pinched) return false
  return now - enteredAt < HINT_MS
}

export type Vec3 = [number, number, number]

/**
 * Where the hint sits: in front of the head along its gaze, a little below
 * eye level so it never covers the panels, and never behind or below the
 * person (the gaze is flattened, so looking down puts it in front, not at
 * the floor).
 */
export function hintTarget(head: Vec3, gaze: Vec3, distance = 0.9, drop = 0.12): Vec3 {
  const [gx, , gz] = gaze
  const flat = Math.hypot(gx, gz)
  // Looking straight up or down: keep the hint ahead of the person.
  const [dx, dz] = flat < 1e-6 ? [0, -1] : [gx / flat, gz / flat]
  return [head[0] + dx * distance, head[1] - drop, head[2] + dz * distance]
}

/** A lazy follow: a step of `alpha` toward the target each frame, so the hint trails the head instead of sticking to it. */
export function approach(current: Vec3, target: Vec3, alpha: number): Vec3 {
  const t = Math.min(1, Math.max(0, alpha))
  return [current[0] + (target[0] - current[0]) * t, current[1] + (target[1] - current[1]) * t, current[2] + (target[2] - current[2]) * t]
}
