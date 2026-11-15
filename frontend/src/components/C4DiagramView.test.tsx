import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { C4DiagramView } from './C4DiagramView'
import { layoutC4 } from '../lib/c4Layout'
import { DIAGRAM, EVIL } from '../test/documentationFixtures'

describe('layoutC4', () => {
  it('layers elements along the edges, the same way every time', () => {
    const first = layoutC4(DIAGRAM)
    expect(layoutC4(DIAGRAM)).toEqual(first)
    const y = Object.fromEntries(first.nodes.map(n => [n.id, n.y]))
    expect(y.n4).toBeLessThan(y.n1)
    expect(y.n3).toBeLessThan(y.n1)
    expect(y.n1).toBeLessThan(y.n2)
    expect(first.edges).toHaveLength(3)
  })

  it('survives a cycle and an edge to an unknown element', () => {
    const cyclic = {
      ...DIAGRAM,
      nodes: DIAGRAM.nodes.slice(0, 2),
      edges: [{ source: 'n1', target: 'n2', label: 'a' }, { source: 'n2', target: 'n1', label: 'b' }, { source: 'n1', target: 'zz', label: 'c' }],
    }
    const layout = layoutC4(cyclic)
    expect(layout.nodes).toHaveLength(2)
    expect(layout.edges).toHaveLength(2)
  })
})

describe('C4DiagramView', () => {
  it('draws the elements as SVG text, never markup, and shows the Mermaid source on demand', () => {
    const { container } = render(<C4DiagramView diagram={DIAGRAM} />)
    expect(screen.getAllByTestId('c4-node')).toHaveLength(4)
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.textContent).toContain(EVIL.slice(0, 20))
    expect(screen.getByText('Each module is shown as a container.')).toBeInTheDocument()
    expect(screen.queryByTestId('mermaid-source')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show the Mermaid source' }))
    expect(screen.getByTestId('mermaid-source')).toHaveTextContent('flowchart TB')
  })
})
