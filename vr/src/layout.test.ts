import { describe, expect, it } from 'vitest'
import { arc, fit, grid } from './layout'

const close = (value: number, expected: number) => expect(value).toBeCloseTo(expected, 5)

describe('the seated layout', () => {
  it('puts one panel straight ahead at eye height, facing the person', () => {
    const [panel] = arc(1, { radius: 2, height: 1.2 })
    expect(panel.position).toEqual([0, 1.2, -2])
    expect(panel.rotationY).toBe(0)
  })

  it('spreads panels on an arc around the person, each turned to face them', () => {
    const panels = arc(3, { radius: 1, spread: Math.PI / 2, height: 1 })
    close(panels[0].position[0], -Math.SQRT1_2)
    close(panels[0].position[2], -Math.SQRT1_2)
    close(panels[0].rotationY, Math.PI / 4)
    expect(panels[1].position).toEqual([0, 1, -1])
    close(panels[2].position[0], Math.SQRT1_2)
    close(panels[2].rotationY, -Math.PI / 4)
    expect(arc(0)).toEqual([])
  })

  it('fills a panel top-down in columns, centred', () => {
    const column = grid(3, { cols: 1, width: 0.5, height: 0.1, gap: 0.02 })
    expect(column.map(([x]) => x)).toEqual([0, 0, 0])
    close(column[0][1], 0.12)
    close(column[1][1], 0)
    close(column[2][1], -0.12)
    const [first, second, third] = grid(3, { cols: 2, width: 0.4, height: 0.1, gap: 0.02 })
    close(first[0], -0.21)
    close(second[0], 0.21)
    close(first[1], 0.06)
    close(third[1], -0.06)
    expect(grid(0)).toEqual([])
  })

  it('shortens a label at a word', () => {
    expect(fit('Create testing strategy document', 20)).toBe('Create testing…')
    expect(fit('Short', 20)).toBe('Short')
    expect(fit('Supercalifragilisticexpialidocious words', 12)).toBe('Supercalifr…')
  })
})
