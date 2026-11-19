import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { UsageTeamHistory } from './UsageTeamHistory'

describe('UsageTeamHistory', () => {
  it('names the period that keeps today\'s team', () => {
    render(
      <UsageTeamHistory
        history={{
          started_at: '2026-11-05T00:00:00Z',
          current_teams_cover: { since: '2026-11-01', before: '2026-11-05T00:00:00Z' },
        }}
      />,
    )
    const note = screen.getByTestId('usage-team-history')
    expect(note.textContent).toContain('2026-11-01')
    expect(note.textContent).toContain('2026-11-05T00:00:00Z')
  })

  it('says the teams are those at the time when the history covers the range', () => {
    render(<UsageTeamHistory history={{ started_at: '2026-11-05T00:00:00Z', current_teams_cover: null }} />)
    expect(screen.getByTestId('usage-team-history').textContent).toMatch(/at the time of the work/)
  })

  it('renders nothing for a report without the field', () => {
    const { container } = render(<UsageTeamHistory />)
    expect(container.textContent).toBe('')
  })
})
