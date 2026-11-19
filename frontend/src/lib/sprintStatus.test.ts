import { describe, expect, it } from 'vitest'
import { shownSprintStatus } from './sprintStatus'

describe('shownSprintStatus', () => {
  it('shows a completed sprint as integrating while its integration runs', () => {
    expect(shownSprintStatus('completed', 'integrating')).toBe('integrating')
  })

  it('shows a completed sprint whose integration failed as such', () => {
    expect(shownSprintStatus('completed', 'failed')).toBe('integration_failed')
  })

  it.each(['integrated', 'none', null, undefined] as const)(
    'shows completed when the integration state is %s',
    state => {
      expect(shownSprintStatus('completed', state)).toBe('completed')
    },
  )

  it('leaves any other status as it is', () => {
    expect(shownSprintStatus('active', 'failed')).toBe('active')
    expect(shownSprintStatus('paused', 'integrating')).toBe('paused')
  })
})
