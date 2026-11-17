import { describe, expect, it } from 'vitest'
import type { BacklogItem, ObservedFacts } from '../services/api'
import {
  EMPTY_FILTERS, adjacentStatus, filterColumns, filtersActive, latestMessages, matchesFilters, needsPerson,
  relativeTime, roleInitials, roleTone, rolesOf,
} from './kanban'

const NOTHING: ObservedFacts = { verdict: null, risk_tier: null, risk_areas: [], protected_paths: [], waits_for_person: false }

function item(id: number, extra: Partial<BacklogItem> = {}): BacklogItem {
  return {
    id, sprint_id: 7, title: `Task ${id}`, status: 'todo', priority: 'medium', assignee: 'Developer',
    comments: [], created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', ...extra,
  }
}

describe('the board helpers', () => {
  it('moves one column at a time and stops at the edges', () => {
    expect(adjacentStatus('todo', 1)).toBe('in_progress')
    expect(adjacentStatus('in_review', -1)).toBe('in_progress')
    expect(adjacentStatus('todo', -1)).toBeNull()
    expect(adjacentStatus('blocked', 1)).toBeNull()
  })

  it('reads "needs a person" only from what was observed', () => {
    expect(needsPerson(item(1))).toBe(false)
    expect(needsPerson(item(1, { observed: NOTHING }))).toBe(false)
    expect(needsPerson(item(1, { observed: { ...NOTHING, waits_for_person: true } }))).toBe(true)
    expect(needsPerson(item(1, { observed: { ...NOTHING, protected_paths: ['bench/x.json'] } }))).toBe(true)
    // An agent saying a person must look is a message, not an observation.
    expect(needsPerson(item(1, { comments: [{ author: 'Developer', text: 'a person must review', created_at: '' }] }))).toBe(false)
  })

  it('filters by text, number, role and preset', () => {
    const risky = item(2, { title: 'Encrypt tokens', assignee: 'SecOps', observed: { ...NOTHING, risk_tier: 'high' } })
    const waiting = item(3, { title: 'Checkout', waiting_for: [2] })
    expect(filtersActive(EMPTY_FILTERS)).toBe(false)
    expect(matchesFilters(risky, { ...EMPTY_FILTERS, search: 'encrypt' })).toBe(true)
    expect(matchesFilters(risky, { ...EMPTY_FILTERS, search: '#2' })).toBe(true)
    expect(matchesFilters(waiting, { ...EMPTY_FILTERS, search: 'encrypt' })).toBe(false)
    expect(matchesFilters(risky, { ...EMPTY_FILTERS, role: 'SecOps' })).toBe(true)
    expect(matchesFilters(waiting, { ...EMPTY_FILTERS, role: 'SecOps' })).toBe(false)
    expect(matchesFilters(risky, { ...EMPTY_FILTERS, preset: 'highRisk' })).toBe(true)
    expect(matchesFilters(waiting, { ...EMPTY_FILTERS, preset: 'highRisk' })).toBe(false)
    expect(matchesFilters(waiting, { ...EMPTY_FILTERS, preset: 'waiting' })).toBe(true)

    const columns = { todo: [risky, waiting], done: [] }
    expect(filterColumns(columns, EMPTY_FILTERS)).toBe(columns)
    expect(filterColumns(columns, { ...EMPTY_FILTERS, preset: 'waiting' })).toEqual({ todo: [waiting], done: [] })
    expect(rolesOf(columns)).toEqual(['Developer', 'SecOps'])
  })

  it('lists the latest messages first, at most the limit', () => {
    const a = item(1, { comments: [{ author: 'QA', text: 'old', created_at: '2026-10-01T00:00:00Z' }] })
    const b = item(2, { comments: [{ author: 'System', text: 'new', created_at: '2026-10-02T00:00:00Z' }] })
    expect(latestMessages({ todo: [a], blocked: [b] }).map(m => m.text)).toEqual(['new', 'old'])
    expect(latestMessages({ todo: [a], blocked: [b] }, 1)).toHaveLength(1)
  })

  it('gives a role two letters and always the same tone', () => {
    expect(roleInitials('BackendDeveloper')).toBe('BD')
    expect(roleInitials('QA')).toBe('QA')
    expect(roleInitials('Developer')).toBe('DE')
    expect(roleTone('QA')).toBe(roleTone('QA'))
  })

  it('says how long ago, reading the backend naive UTC timestamps', () => {
    const now = Date.parse('2026-10-01T12:00:00Z')
    expect(relativeTime('2026-10-01 11:48:00', 'en', now)).toBe('12 min. ago')
    expect(relativeTime('2026-10-01T09:00:00Z', 'en', now)).toBe('3 hr. ago')
    expect(relativeTime('not a date', 'en', now)).toBeNull()
    expect(relativeTime(undefined, 'en', now)).toBeNull()
  })
})
