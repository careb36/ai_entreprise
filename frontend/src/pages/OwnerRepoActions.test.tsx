import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import OwnerActions from './OwnerActions'
import { queryClient } from '../lib/queryClient'
import { useAppStore } from '../store/useAppStore'
import { useAuthStore } from '../store/useAuthStore'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body, headers: new Headers() } as Response
}

const action = (name: string) => ({
  name, issue: 701, available: true, reason: null, running_run_id: null, last_runs: [], observes: true,
  required: [], records: [], params: {},
})
const catalogue = { actions: [action('land_branch'), action('push_main'), action('close_issue')] }
const MAIN = 'a'.repeat(40)
const HEAD = 'b'.repeat(40)
const FIX = 'd'.repeat(40)

const landPreview = {
  action: 'land_branch', params: { branch: 'feat/x' },
  effect: [{ key: 'merge', values: { branch: 'feat/x', count: 1 } }, { key: 'protected', values: { count: 1 } }, { key: 'no_push', values: {} }],
  cost: { models: [], token_budget: 0, max_seconds: 6000 },
  facts: { head: HEAD, main: MAIN, commits: [{ sha: HEAD, subject: 'Change the rules' }], commits_truncated: false,
    diffstat: [{ path: 'docs/POSITIONING.md', added: 2, deleted: 1 }],
    protected: [{ path: 'docs/POSITIONING.md', pattern: 'docs/POSITIONING.md' }], needs_owner: true },
  blockers: [], step_up: true, confirmation: 'land-digest',
}
const pushPreview = {
  action: 'push_main', params: {},
  effect: [{ key: 'public', values: {} }, { key: 'push', values: { count: 1, main: MAIN.slice(0, 12), remote: 'origin' } }],
  cost: { models: [], token_budget: 0, max_seconds: 420 },
  facts: { main: MAIN, commits: [{ sha: MAIN, subject: 'Land feat/x into main' }], commits_truncated: false },
  blockers: [], step_up: false, confirmation: 'push-digest',
}
const closePreview = (comment: string, digest: string, onOrigin = true, override = false) => ({
  action: 'close_issue', params: { issue_number: 9, fix_commit: FIX, comment, override_not_on_origin: override },
  effect: [{ key: 'comment', values: { number: 9, repository: 'acme/fab' } }, { key: 'close', values: { number: 9 } }],
  cost: { models: [], token_budget: 0, max_seconds: 60 },
  facts: { repository: 'acme/fab', issue: { number: 9, title: 'Broken', url: 'https://github.com/acme/fab/issues/9' },
    fix_commit: FIX, fix_subject: 'Fix it (#9)', fix_on_origin: onOrigin },
  blockers: onOrigin || override ? [] : ['fix_not_on_origin'], step_up: false, confirmation: digest,
})

type Handler = (url: string, body: Record<string, unknown> | null) => Response | undefined

function stub(handler: Handler) {
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : null
    const answered = handler(url, body)
    if (answered) return answered
    if (url.includes('/land_branch/options')) return response(200, { main: MAIN, branches: [{ name: 'feat/x', head: HEAD, subject: 'Change the rules' }] })
    if (url.includes('/close_issue/options')) return response(200, { repository: 'acme/fab', issues: [{ number: 9, title: 'Broken', url: null }] })
    if (init?.method === 'POST' && url.endsWith('/runs')) return response(202, { id: 3, status: 'running', observations: {}, params: {} })
    return response(200, catalogue)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

const starts = (fetch: ReturnType<typeof stub>) =>
  fetch.mock.calls.filter(c => String(c[0]).endsWith('/runs') && (c[1] as RequestInit)?.method === 'POST')
    .map(c => JSON.parse(String((c[1] as RequestInit).body)))

function renderOwner() {
  useAuthStore.setState({ expiresAt: Date.now() + 60_000, memberships: [{ company_id: 4, human_role: 'Owner', effective_role: 'Owner' }] })
  return render(<QueryClientProvider client={queryClient}><OwnerActions /></QueryClientProvider>)
}

function card(title: string) {
  return screen.getByRole('heading', { name: new RegExp(title) }).closest('section') as HTMLElement
}

describe('Owner actions on the repository', () => {
  beforeEach(() => {
    queryClient.clear()
    useAppStore.setState({ companyId: 4, companyName: 'Acme' })
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('a protected land shows the protected paths and asks for the password it sends once', async () => {
    const fetch = stub(url => (url.includes('/land_branch/preview') ? response(200, landPreview) : undefined))
    renderOwner()
    await screen.findByRole('heading', { name: /Land a branch/ })
    const land = card('Land a branch')
    fireEvent.change(await within(land).findByLabelText('Branch to land'), { target: { value: 'feat/x' } })
    fireEvent.click(within(land).getByRole('button', { name: 'Review cost and effect' }))
    const dialog = await screen.findByRole('dialog', { name: 'Run Land a branch?' })
    expect(within(dialog).getByText('docs/POSITIONING.md (docs/POSITIONING.md)')).toBeInTheDocument()
    expect(within(dialog).getByText(/LAND_OWNER=1 is set for this run only/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Nothing is pushed/)).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Your password, to confirm this protected land'), { target: { value: 'secret-pass' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(starts(fetch)).toHaveLength(1))
    expect(starts(fetch)[0]).toEqual({ company_id: 4, params: { branch: 'feat/x' }, confirmation: 'land-digest', step_up_password: 'secret-pass' })
  })

  it('a push says the repository is public and lists the commits; a blocked push cannot be confirmed', async () => {
    let blocked = false
    const fetch = stub(url => (url.includes('/push_main/preview')
      ? response(200, blocked ? { ...pushPreview, blockers: ['not_fast_forward'] } : pushPreview) : undefined))
    renderOwner()
    await screen.findByRole('heading', { name: /Push main to GitHub/ })
    fireEvent.click(within(card('Push main to GitHub')).getByRole('button', { name: 'Review cost and effect' }))
    let dialog = await screen.findByRole('dialog', { name: 'Run Push main to GitHub?' })
    expect(within(dialog).getByRole('alert')).toHaveTextContent('This repository is PUBLIC')
    expect(within(dialog).getByText(`${MAIN.slice(0, 12)} Land feat/x into main`)).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    blocked = true
    fireEvent.click(within(card('Push main to GitHub')).getByRole('button', { name: 'Review cost and effect' }))
    dialog = await screen.findByRole('dialog', { name: 'Run Push main to GitHub?' })
    expect(within(dialog).getByText(/not a fast-forward/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Confirm' })).toBeDisabled()
    expect(starts(fetch)).toHaveLength(0)
  })

  it('an edited comment is previewed again, and the start sends exactly the previewed text', async () => {
    const edited = 'Fixed on main.\n\nThanks!'
    const fetch = stub((url, body) => {
      if (!url.includes('/close_issue/preview')) return undefined
      const params = (body?.params ?? {}) as Record<string, unknown>
      return response(200, params.comment ? closePreview(String(params.comment), 'digest-2') : closePreview(`Fixed by ${FIX} (Fix it (#9)). It is on origin/main.`, 'digest-1'))
    })
    renderOwner()
    await screen.findByRole('heading', { name: /Close an issue/ })
    const close = card('Close an issue')
    fireEvent.change(await within(close).findByLabelText('Issue to close'), { target: { value: '9' } })
    fireEvent.click(within(close).getByRole('button', { name: 'Review cost and effect' }))
    const dialog = await screen.findByRole('dialog', { name: 'Run Close an issue?' })
    const text = within(dialog).getByLabelText('Comment posted on the issue (exactly this text)')
    expect(text).toHaveValue(`Fixed by ${FIX} (Fix it (#9)). It is on origin/main.`)
    fireEvent.change(text, { target: { value: edited } })
    expect(within(dialog).getByRole('button', { name: 'Confirm' })).toBeDisabled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Preview this text' }))
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Confirm' })).toBeEnabled())
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(starts(fetch)).toHaveLength(1))
    expect(starts(fetch)[0]).toEqual({
      company_id: 4, confirmation: 'digest-2',
      params: { issue_number: 9, fix_commit: FIX, comment: edited, override_not_on_origin: false },
    })
  })

  it('a fix not on origin blocks the close until the override is ticked and previewed', async () => {
    const fetch = stub((url, body) => {
      if (!url.includes('/close_issue/preview')) return undefined
      const params = (body?.params ?? {}) as Record<string, unknown>
      return response(200, closePreview('Fixed.', params.override_not_on_origin ? 'digest-o' : 'digest-1', false, Boolean(params.override_not_on_origin)))
    })
    renderOwner()
    await screen.findByRole('heading', { name: /Close an issue/ })
    const close = card('Close an issue')
    fireEvent.change(await within(close).findByLabelText('Issue to close'), { target: { value: '9' } })
    fireEvent.click(within(close).getByRole('button', { name: 'Review cost and effect' }))
    const dialog = await screen.findByRole('dialog', { name: 'Run Close an issue?' })
    expect(within(dialog).getByText(/The fix is not on origin\/main/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Confirm' })).toBeDisabled()
    fireEvent.click(within(dialog).getByLabelText('Close it although the fix is not on origin yet'))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Preview this text' }))
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Confirm' })).toBeEnabled())
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(starts(fetch)).toHaveLength(1))
    expect(starts(fetch)[0].params.override_not_on_origin).toBe(true)
    expect(starts(fetch)[0].confirmation).toBe('digest-o')
  })
})
