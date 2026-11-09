import { describe, it, expect } from 'vitest'
import {
  NAVIGATION_CATEGORIES,
  findActiveCategory,
  isPathActive,
} from './navSchema'

describe('navSchema (Hick\'s Law Categorization)', () => {
  it('defines exactly 5 top-level categories', () => {
    expect(NAVIGATION_CATEGORIES).toHaveLength(5)
    const ids = NAVIGATION_CATEGORIES.map(c => c.id)
    expect(ids).toEqual([
      'dashboard',
      'graph_topology',
      'orchestration',
      'audit',
      'settings',
    ])
  })

  it('identifies active categories accurately across nested subpaths', () => {
    expect(findActiveCategory('/sprints')).toBe('dashboard')
    expect(findActiveCategory('/sprints/sprint-1/tasks/task-2')).toBe('dashboard')
    expect(findActiveCategory('/generate')).toBe('graph_topology')
    expect(findActiveCategory('/memory')).toBe('orchestration')
    expect(findActiveCategory('/evidence')).toBe('audit')
    expect(findActiveCategory('/governance')).toBe('audit')
    expect(findActiveCategory('/company')).toBe('settings')
    expect(findActiveCategory('/agents')).toBe('settings')
    expect(findActiveCategory('/support')).toBe('settings')
    expect(findActiveCategory('/unknown-route')).toBeNull()
  })

  it('evaluates exact and prefix path matching', () => {
    expect(isPathActive('/evidence', '/evidence')).toBe(true)
    expect(isPathActive('/evidence/123', '/evidence')).toBe(true)
    expect(isPathActive('/evidence-other', '/evidence')).toBe(false)
    expect(isPathActive('/evidence/123', '/evidence', true)).toBe(false)
  })
})
