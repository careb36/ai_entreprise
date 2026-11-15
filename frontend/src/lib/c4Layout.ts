/**
 * Where each element of a C4 diagram goes: a layered layout computed from the
 * diagram's data, so the page draws it as SVG without a diagram library and
 * without ever parsing diagram source (the Mermaid text is shown, not run).
 *
 * Layers follow the edges (the longest path from an element nothing points
 * to); a cycle is broken where the walk meets it. Within a layer, elements
 * keep their group, then their order in the data, so the same diagram always
 * gets the same picture.
 */
import type { C4Diagram, C4Node } from '../services/documentation'

export const NODE_WIDTH = 184
export const NODE_HEIGHT = 64
const GAP_X = 36
const GAP_Y = 72
const MARGIN = 16

export interface PlacedNode extends C4Node {
  x: number
  y: number
}

export interface PlacedEdge {
  source: string
  target: string
  label: string
  x1: number
  y1: number
  x2: number
  y2: number
}

export interface C4Layout {
  nodes: PlacedNode[]
  edges: PlacedEdge[]
  width: number
  height: number
}

function layers(diagram: C4Diagram): Map<string, number> {
  const ids = new Set(diagram.nodes.map(n => n.id))
  const outgoing = new Map<string, string[]>()
  const incoming = new Set<string>()
  for (const edge of diagram.edges) {
    if (!ids.has(edge.source) || !ids.has(edge.target) || edge.source === edge.target) continue
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target])
    incoming.add(edge.target)
  }
  const layer = new Map<string, number>()
  const onPath = new Set<string>()
  const visit = (id: string, depth: number) => {
    if (onPath.has(id) || (layer.get(id) ?? -1) >= depth) return
    layer.set(id, depth)
    onPath.add(id)
    for (const next of outgoing.get(id) ?? []) visit(next, depth + 1)
    onPath.delete(id)
  }
  for (const node of diagram.nodes) if (!incoming.has(node.id)) visit(node.id, 0)
  // Elements only reachable through a cycle start at the top.
  for (const node of diagram.nodes) if (!layer.has(node.id)) visit(node.id, 0)
  return layer
}

export function layoutC4(diagram: C4Diagram): C4Layout {
  const layer = layers(diagram)
  const groupOrder = new Map(diagram.groups.map((g, i) => [g.id, i]))
  const rows: C4Node[][] = []
  diagram.nodes.forEach(node => {
    const row = layer.get(node.id) ?? 0
    ;(rows[row] ??= []).push(node)
  })
  const placed = new Map<string, PlacedNode>()
  const widest = Math.max(1, ...rows.map(r => r?.length ?? 0))
  const width = MARGIN * 2 + widest * NODE_WIDTH + (widest - 1) * GAP_X
  rows.forEach((row, r) => {
    if (!row) return
    const sorted = [...row].sort((a, b) => (groupOrder.get(a.group ?? '') ?? -1) - (groupOrder.get(b.group ?? '') ?? -1))
    const rowWidth = sorted.length * NODE_WIDTH + (sorted.length - 1) * GAP_X
    const start = (width - rowWidth) / 2
    sorted.forEach((node, i) => {
      placed.set(node.id, { ...node, x: start + i * (NODE_WIDTH + GAP_X), y: MARGIN + r * (NODE_HEIGHT + GAP_Y) })
    })
  })
  const edges: PlacedEdge[] = []
  for (const edge of diagram.edges) {
    const a = placed.get(edge.source)
    const b = placed.get(edge.target)
    if (!a || !b || a === b) continue
    const down = b.y > a.y
    const up = b.y < a.y
    if (down || up) {
      edges.push({
        ...edge,
        x1: a.x + NODE_WIDTH / 2,
        y1: down ? a.y + NODE_HEIGHT : a.y,
        x2: b.x + NODE_WIDTH / 2,
        y2: down ? b.y : b.y + NODE_HEIGHT,
      })
    } else {
      // Same row: from the facing sides.
      const right = b.x > a.x
      edges.push({
        ...edge,
        x1: right ? a.x + NODE_WIDTH : a.x,
        y1: a.y + NODE_HEIGHT / 2,
        x2: right ? b.x : b.x + NODE_WIDTH,
        y2: b.y + NODE_HEIGHT / 2,
      })
    }
  }
  const height = MARGIN * 2 + Math.max(1, rows.length) * NODE_HEIGHT + Math.max(0, rows.length - 1) * GAP_Y
  return { nodes: [...placed.values()], edges, width, height }
}
