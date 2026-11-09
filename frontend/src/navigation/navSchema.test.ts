import { describe, it, expect } from 'vitest'
import {
  NAVIGATION_CATEGORIES,
  getActiveCategory,
  findSubItemByPath,
} from './navSchema'

describe('Global Navigation Schema (Hick\'s Law Compliance)', () => {
  it('enforces Hick\'s Law: exactly 5 top-level categories', () => {
    expect(NAVIGATION_CATEGORIES).toHaveLength(5)
  })

  it('contains the 5 designated functional domains', () => {
    const categoryIds = NAVIGATION_CATEGORIES.map((c) => c.id)
    expect(categoryIds).toEqual([
      'dashboard',
      'graph_topology',
      'orchestration',
      'audit',
      'settings',
    ])
  })

  it('provides a valid default path and non-empty items array for each category', () => {
    for (const cat of NAVIGATION_CATEGORIES) {
      expect(cat.defaultPath).toBeTruthy()
      expect(cat.defaultPath.startsWith('/')).toBe(true)
      expect(cat.items.length).toBeGreaterThan(0)
      for (const item of cat.items) {
        expect(item.id).toBeTruthy()
        expect(item.to.startsWith('/')).toBe(true)
        expect(item.defaultLabel).toBeTruthy()
        expect(item.icon).toBeDefined()
      }
    }
  })

  it('correctly resolves active category for deep routes', () => {
    expect(getActiveCategory('/sprints')?.id).toBe('dashboard')
    expect(getActiveCategory('/board')?.id).toBe('dashboard')
    expect(getActiveCategory('/generate')?.id).toBe('graph_topology')
    expect(getActiveCategory('/memory')?.id).toBe('orchestration')
    expect(getActiveCategory('/evidence')?.id).toBe('audit')
    expect(getActiveCategory('/audit')?.id).toBe('audit')
    expect(getActiveCategory('/company')?.id).toBe('settings')
    expect(getActiveCategory('/agents')?.id).toBe('settings')
  })

  it('returns undefined for unmatched paths in getActiveCategory', () => {
    expect(getActiveCategory('/unmatched-unknown-path')).toBeUndefined()
  })

  it('finds sub-item by exact or prefix match', () => {
    const sub = findSubItemByPath('/evidence')
    expect(sub).toBeDefined()
    expect(sub?.id).toBe('evidence-log')
    expect(sub?.defaultLabel).toBe('Verification Evidence')
  })
})
