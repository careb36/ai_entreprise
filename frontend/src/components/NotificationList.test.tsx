import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { NotificationList, notificationLink } from './NotificationList'
import { NotificationPreferences } from './NotificationPreferences'
import { NotificationBell } from './NotificationBell'
import { queryClient } from '../lib/queryClient'
import type { PersonalNotification } from '../services/api'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

const SECURITY: PersonalNotification = {
  id: 1, company_id: 1, kind: 'sso_break_glass_exclusion', security: true,
  subject: { type: 'sso_team_exclusion', id: 9 }, context: { team_id: 3, user_id: 4 },
  created_at: '2026-11-20T10:00:00', read_at: null, snoozed_until: null,
}
const REVIEW: PersonalNotification = {
  id: 2, company_id: 1, kind: 'review_requested', security: false,
  subject: { type: 'task', id: 12 }, context: { sprint_id: 5 },
  created_at: '2026-11-20T09:00:00', read_at: null, snoozed_until: null,
}

const PREFERENCES = {
  email_available: true,
  preferences: [
    { kind: 'sso_domain_lapsed', security: true, in_app: true, email: true, in_app_locked: true },
    { kind: 'review_requested', security: false, in_app: true, email: false, in_app_locked: false },
  ],
}

function stubApi(overrides: Record<string, (init: RequestInit) => Response> = {}) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const { pathname } = new URL(url)
    const key = `${init.method ?? 'GET'} ${pathname}`
    if (overrides[key]) return overrides[key](init)
    if (key === 'GET /v1/notifications') return response(200, { notifications: [SECURITY, REVIEW], unread: 2, kinds: [] })
    if (key === 'GET /v1/notifications/count') return response(200, { unread: 2 })
    if (key === 'GET /v1/notifications/preferences') return response(200, PREFERENCES)
    return response(404, { detail: 'Not found' })
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderWith(node: React.ReactNode) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{node}</MemoryRouter>
    </QueryClientProvider>,
  )
}

function bodyOf(fetch: ReturnType<typeof stubApi>, suffix: string) {
  const call = fetch.mock.calls.find(([url, init]) => String(url).endsWith(suffix) && init?.method === 'POST')
  return JSON.parse(String(call?.[1]?.body))
}

describe('NotificationList', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('lists the notices with the security notice named in words', async () => {
    stubApi()
    renderWith(<NotificationList />)
    expect(await screen.findByText(/took a member out of an SSO-managed team/)).toBeInTheDocument()
    expect(screen.getByText('Security notice')).toBeInTheDocument()
    expect(screen.getByText('A delivery waits for a person to accept it')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(2)
  })

  it('marks a notice read', async () => {
    const fetch = stubApi({ 'POST /v1/notifications/2/read': () => response(200, { ...REVIEW, read_at: '2026-11-20T11:00:00' }) })
    renderWith(<NotificationList />)
    const buttons = await screen.findAllByRole('button', { name: 'Mark read' })
    fireEvent.click(buttons[1])
    await waitFor(() => expect(bodyOf(fetch, '/v1/notifications/2/read')).toEqual({ read: true }))
  })

  it('offers a security notice a day of snooze at most', async () => {
    const fetch = stubApi({ 'POST /v1/notifications/1/snooze': () => response(200, SECURITY) })
    renderWith(<NotificationList />)
    const [security, review] = await screen.findAllByRole('combobox', { name: /Snooze/ })
    const hours = (select: HTMLElement) => Array.from(select.querySelectorAll('option')).map(o => o.value).filter(Boolean)
    expect(hours(security)).toEqual(['1', '4', '24'])
    expect(hours(review)).toEqual(['1', '4', '24', '72', '168'])
    fireEvent.change(security, { target: { value: '4' } })
    await waitFor(() => expect(bodyOf(fetch, '/v1/notifications/1/snooze').until).toBeTruthy())
  })

  it('shows the backend refusal in words', async () => {
    stubApi({ 'POST /v1/notifications/read': () => response(422, { detail: 'already_read' }) })
    renderWith(<NotificationList />)
    fireEvent.click(await screen.findByRole('button', { name: 'Mark all read' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This notice is already read.')
  })

  it('links each kind to the page that shows its subject', () => {
    expect(notificationLink(SECURITY)).toBe('/company/teams')
    expect(notificationLink(REVIEW)).toBe('/sprints/5/tasks/12')
    expect(notificationLink({ ...REVIEW, kind: 'integration_waiting', context: {} })).toBe('/governance')
    expect(notificationLink({ ...REVIEW, kind: 'sso_domain_lapsed' })).toBe('/company/sso')
  })
})

describe('NotificationPreferences', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('keeps a security notice in the app and saves an email choice', async () => {
    const fetch = stubApi({
      'PUT /v1/notifications/preferences/review_requested': () => response(200, PREFERENCES.preferences[1]),
    })
    renderWith(<NotificationPreferences />)
    const inApp = await screen.findAllByRole('checkbox', { name: 'In the app' })
    expect(inApp[0]).toBeDisabled()
    expect(inApp[0]).toBeChecked()
    expect(screen.getByText(/always shown in the app/)).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('checkbox', { name: 'By email' })[1])
    await waitFor(() => {
      const call = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({ in_app: true, email: true })
    })
  })
})

describe('NotificationBell', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    vi.unstubAllGlobals()
    queryClient.clear()
  })

  it('names the unread count in its accessible name', async () => {
    stubApi()
    renderWith(<NotificationBell enabled />)
    expect(await screen.findByRole('link', { name: 'Notifications: 2 unread' })).toHaveAttribute('href', '/notifications')
  })

  it('stays away when signed out', () => {
    const fetch = stubApi()
    renderWith(<NotificationBell enabled={false} />)
    expect(screen.queryByRole('link')).toBeNull()
    expect(fetch).not.toHaveBeenCalled()
  })
})
