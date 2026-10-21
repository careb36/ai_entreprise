/**
 * Where things go in a seated room, in metres, with no rendering.
 *
 * The person sits at the origin looking down -Z. Panels stand on an arc in
 * front of them at eye height, and cards fill a panel top-down in columns,
 * so everything is within an arm's reach and a small turn of the head: the
 * competition asks for seated-optimized experiences.
 */

export interface Placement {
  position: [number, number, number]
  /** Rotation around Y so the panel faces the person. */
  rotationY: number
}

export interface ArcOptions {
  radius?: number
  /** The angle the panels span, in radians. */
  spread?: number
  height?: number
}

/** `n` panels on an arc in front of a seated person, evenly spread and facing them. */
export function arc(n: number, { radius = 1.6, spread = Math.PI / 2, height = 1.3 }: ArcOptions = {}): Placement[] {
  if (n <= 0) return []
  const step = n === 1 ? 0 : spread / (n - 1)
  const first = n === 1 ? 0 : -spread / 2
  return Array.from({ length: n }, (_, index) => {
    const angle = first + index * step
    return {
      position: [radius * Math.sin(angle), height, -radius * Math.cos(angle)],
      // 0 - angle, not -angle: the middle panel's rotation is +0, not -0.
      rotationY: 0 - angle,
    }
  })
}

export interface GridOptions {
  cols?: number
  width?: number
  height?: number
  gap?: number
}

/** Local x/y offsets of `count` cards laid out top-down in `cols` columns, centred on the panel. */
export function grid(count: number, { cols = 1, width = 0.5, height = 0.12, gap = 0.03 }: GridOptions = {}): [number, number][] {
  if (count <= 0) return []
  const columns = Math.max(1, cols)
  const rows = Math.ceil(count / columns)
  const totalWidth = columns * width + (columns - 1) * gap
  const totalHeight = rows * height + (rows - 1) * gap
  return Array.from({ length: count }, (_, index) => {
    const col = index % columns
    const row = Math.floor(index / columns)
    const x = -totalWidth / 2 + width / 2 + col * (width + gap)
    const y = totalHeight / 2 - height / 2 - row * (height + gap)
    return [x, y]
  })
}

/** A short label that fits a card: whole words, then an ellipsis. */
export function fit(text: string, max = 28): string {
  if (text.length <= max) return text
  const cut = text.slice(0, max - 1)
  const space = cut.lastIndexOf(' ')
  return `${space > max / 2 ? cut.slice(0, space) : cut}…`
}
