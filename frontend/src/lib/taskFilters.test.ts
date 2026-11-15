import { describe, expect, it } from 'vitest'
import { FILTER_FIELDS } from '../services/api'
import { FIELD_OPERATORS, emptyRow, filterToRows, rowToCondition, rowsToFilter } from './taskFilters'

describe('taskFilters', () => {
  it('knows the operators of every field the server allows', () => {
    expect(Object.keys(FIELD_OPERATORS).sort()).toEqual([...FILTER_FIELDS].sort())
    expect(FIELD_OPERATORS.waits_for_person).toEqual(['eq', 'ne'])
    expect(FIELD_OPERATORS.risk_tier).toContain('gte')
  })

  it('types each value for its field and skips an incomplete row', () => {
    expect(rowToCondition({ field: 'rework_count', op: 'gte', value: '2' })).toEqual({ field: 'rework_count', op: 'gte', value: 2 })
    expect(rowToCondition({ field: 'waits_for_person', op: 'eq', value: 'true' })).toEqual({ field: 'waits_for_person', op: 'eq', value: true })
    expect(rowToCondition({ field: 'verdict', op: 'eq', value: 'none' })).toEqual({ field: 'verdict', op: 'eq', value: null })
    expect(rowToCondition({ field: 'status', op: 'in', value: 'done, blocked' })).toEqual({ field: 'status', op: 'in', value: ['done', 'blocked'] })
    expect(rowToCondition({ field: 'producer', op: 'eq', value: '  ' })).toBeNull()
    expect(rowToCondition({ field: 'rework_count', op: 'eq', value: 'two' })).toBeNull()
  })

  it('joins the rows by AND or OR and keeps the sprint as a condition of its own', () => {
    const rows = [emptyRow('status'), { field: 'covers' as const, op: 'eq' as const, value: 'REQ-3' }]
    expect(rowsToFilter(rows, 'and', 8)).toEqual({
      and: [
        { field: 'sprint_id', op: 'eq', value: 8 },
        { field: 'status', op: 'eq', value: 'todo' },
        { field: 'covers', op: 'eq', value: 'REQ-3' },
      ],
    })
    expect(rowsToFilter(rows, 'or', 8)).toEqual({
      and: [
        { field: 'sprint_id', op: 'eq', value: 8 },
        { or: [{ field: 'status', op: 'eq', value: 'todo' }, { field: 'covers', op: 'eq', value: 'REQ-3' }] },
      ],
    })
    expect(rowsToFilter([], 'and')).toBeNull()
  })

  it('reads a saved flat filter back as rows, and refuses a nested one', () => {
    expect(filterToRows({ or: [{ field: 'verdict', op: 'eq', value: null }] })).toEqual({
      join: 'or', rows: [{ field: 'verdict', op: 'eq', value: 'none' }],
    })
    expect(filterToRows({})).toEqual({ join: 'and', rows: [] })
    expect(filterToRows({ and: [{ or: [{ field: 'status', op: 'eq', value: 'done' }] }] })).toBeNull()
  })
})
