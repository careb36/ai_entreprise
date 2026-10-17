import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import GlassRoom from './GlassRoom'

vi.mock('../hooks/useQueries', () => ({
  useSprintBoard: () => ({ data: undefined }),
  useQuotas: () => ({ data: [] }),
}))

vi.mock('../store/agentStore', () => ({
  useAgentStore: () => ({
    messages: [],
    agentStates: {
      Developer: {
        role: 'Developer',
        fsmState: 'waiting',
        currentActivity: 'Browser agent activity',
        lastSeen: new Date().toISOString(),
      },
    },
    isConnected: true,
    connectionError: null,
    connect: vi.fn(),
    disconnect: vi.fn(),
    memoryHits: 0,
    lastRetro: null,
  }),
}))

afterEach(() => vi.unstubAllGlobals())

describe('GlassRoom scroll clearance', () => {
  it('reserves chat-button clearance in each desktop scroll pane without shrinking the roster', () => {
    vi.stubGlobal('requestAnimationFrame', vi.fn())
    const { container } = render(<GlassRoom sprintId="42" />)

    const scrollPanes = container.querySelectorAll('[class~="xl:pb-24"]')
    expect(scrollPanes).toHaveLength(3)
    for (const pane of scrollPanes) {
      expect(pane.className).toMatch(/(?:lg|xl):overflow-y-auto/)
    }

    const roster = screen.getByText('Browser agent activity').closest('.space-y-px')
    expect(roster).toHaveClass('shrink-0')
    expect(roster).not.toHaveClass('lg:min-h-0')
  })
})
