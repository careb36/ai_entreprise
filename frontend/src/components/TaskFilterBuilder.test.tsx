import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { TaskFilterBuilder } from './TaskFilterBuilder'
import { rowsToFilter, type FilterJoin, type FilterRow } from '../lib/taskFilters'

function Harness({ onFilter }: { onFilter: (value: unknown) => void }) {
  const [rows, setRows] = useState<FilterRow[]>([])
  const [join, setJoin] = useState<FilterJoin>('and')
  return (
    <TaskFilterBuilder rows={rows} join={join} onChange={(next, nextJoin) => {
      setRows(next); setJoin(nextJoin); onFilter(rowsToFilter(next, nextJoin))
    }} />
  )
}

describe('TaskFilterBuilder', () => {
  it('builds a filter from labelled controls, with AND or OR', () => {
    let filter: unknown = null
    render(<Harness onFilter={value => { filter = value }} />)

    fireEvent.click(screen.getByRole('button', { name: 'Add a condition' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Field of condition 1' }), { target: { value: 'verdict' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Value for Verdict' }), { target: { value: 'refuted' } })
    expect(filter).toEqual({ and: [{ field: 'verdict', op: 'eq', value: 'refuted' }] })

    fireEvent.click(screen.getByRole('button', { name: 'Add a condition' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Field of condition 2' }), { target: { value: 'rework_count' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Operator for Rework count' }), { target: { value: 'gte' } })
    fireEvent.change(screen.getByRole('textbox', { name: 'Value for Rework count' }), { target: { value: '2' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Match' }), { target: { value: 'or' } })
    expect(filter).toEqual({
      or: [
        { field: 'verdict', op: 'eq', value: 'refuted' },
        { field: 'rework_count', op: 'gte', value: 2 },
      ],
    })

    // The operators offered are the field's own: no "at least" on a producer.
    fireEvent.change(screen.getByRole('combobox', { name: 'Field of condition 2' }), { target: { value: 'producer' } })
    const ops = screen.getByRole('combobox', { name: 'Operator for Producer' })
    expect([...ops.querySelectorAll('option')].map(o => o.textContent)).toEqual(['is', 'is not', 'is one of'])

    fireEvent.click(screen.getByRole('button', { name: 'Remove condition 2' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove condition 1' }))
    expect(filter).toBeNull()
    expect(screen.queryAllByTestId('filter-row')).toHaveLength(0)
  })
})
