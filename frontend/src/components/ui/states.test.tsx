import { fireEvent, render, screen } from '@testing-library/react'
import { CircleDot } from 'lucide-react'
import { describe, expect, it, vi } from 'vitest'
import {
  AccessNotice, FormActions, LoadError, LoadingLine, LoadingRows, SavedNote, Spinner,
  SprintStatusBadge, TicketStatusBadge, statusLook,
} from '.'

// The page states and the keyed status badges promoted to the design system
// (docs/contexts/frontend.md, Design system). Plain strings stand for what a
// page passes from t().
describe('the page states', () => {
  it('stops every spinner under reduced motion and hides it from screen readers', () => {
    const { container } = render(<><Spinner /><Spinner size={13} /><LoadingLine>Loading wiki</LoadingLine></>)
    const spinners = container.querySelectorAll('svg')
    expect(spinners).toHaveLength(3)
    spinners.forEach((svg) => {
      expect(svg).toHaveAttribute('aria-hidden', 'true')
      expect(svg).toHaveClass('animate-spin', 'motion-reduce:animate-none')
    })
    expect(spinners[1]).toHaveAttribute('width', '13')
    expect(screen.getByRole('status')).toHaveTextContent('Loading wiki')
  })

  it('names the loading placeholders, by default as loading', () => {
    const { rerender } = render(<LoadingRows rows={3} />)
    const rows = screen.getByRole('status')
    expect(rows).toHaveAccessibleName(/loading/i)
    expect(rows.children).toHaveLength(3)
    rerender(<LoadingRows label="Loading teams" />)
    expect(screen.getByRole('status', { name: 'Loading teams' }).children).toHaveLength(2)
  })

  it('shows a load error as an alert, with Retry only when it can retry', () => {
    const onRetry = vi.fn()
    const { rerender } = render(<LoadError onRetry={onRetry}>Could not load: timeout</LoadError>)
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load: timeout')
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalledOnce()
    rerender(<LoadError>Could not load: timeout</LoadError>)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('announces a page the role does not reach, and a save that worked', () => {
    render(<><AccessNotice>Only an Owner sees this.</AccessNotice><FormActions><SavedNote>Saved</SavedNote></FormActions></>)
    const [notice, saved] = screen.getAllByRole('status')
    expect(notice).toHaveTextContent('Only an Owner sees this.')
    expect(notice).toHaveClass('empty-state', 'border-dashed')
    expect(saved).toHaveTextContent('Saved')
    expect(saved.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
  })
})

describe('the keyed status badges', () => {
  it('falls back to the neutral dashed look for a status the map does not know', () => {
    const looks = { open: { icon: CircleDot, tone: 'info' as const } }
    expect(statusLook(looks, 'open')).toBe(looks.open)
    expect(statusLook(looks, 'archived').tone).toBe('neutral')
  })

  it("shows a sprint's and a ticket's status as an icon and a word", () => {
    const { container } = render(
      <>
        <SprintStatusBadge status="active" />
        <SprintStatusBadge status="archived" defaultLabel="Archived" />
        <TicketStatusBadge status="in_progress" />
        <TicketStatusBadge status="escalated" />
      </>,
    )
    expect(screen.getByText('Archived')).toBeInTheDocument()
    expect(screen.getByText('escalated')).toBeInTheDocument()
    const badges = container.querySelectorAll('.status-badge')
    expect(badges).toHaveLength(4)
    badges.forEach((badge) => {
      expect(badge.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
      expect(badge.textContent?.trim()).not.toBe('')
    })
  })
})
