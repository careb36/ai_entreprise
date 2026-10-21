/** The page's optional switches, read from the URL. */
export interface Flags {
  /** `?stats=1`: the frame rate in the room, for the Quest pass. */
  stats: boolean
  /** `?gaze=1`: looking at a card for a moment picks it, an eyes-only alternative to the pinch. */
  gaze: boolean
}

export function parseFlags(search: string): Flags {
  const params = new URLSearchParams(search)
  return { stats: params.get('stats') === '1', gaze: params.get('gaze') === '1' }
}
