import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PersonSections } from './PersonSections'
import { queryClient } from '../../lib/queryClient'
import { useAuthStore } from '../../store/useAuthStore'
import { personSections, statement } from '../../test/documentationFixtures'

const BASE = '/v1/companies/4/documentation/person-sections'

function response(status: number, body: unknown = {}) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

function stubApi(overrides: Record<string, (init: RequestInit) => Response> = {}) {
  const fetch = vi.fn(async (url: string, init: RequestInit = {}) => {
    const key = `${init.method ?? 'GET'} ${new URL(url).pathname}`
    if (overrides[key]) return overrides[key](init)
    if (key === `GET ${BASE}`) return response(200, personSections())
    return response(404)
  })
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function renderFor(document: string, canWrite: boolean) {
  return render(
    <QueryClientProvider client={queryClient}>
      <PersonSections companyId={4} document={document} canWrite={canWrite} />
    </QueryClientProvider>,
  )
}

function row(name: string) {
  return screen.getByRole('heading', { name }).closest('li') as HTMLElement
}

describe('PersonSections', () => {
  beforeEach(() => {
    queryClient.clear()
    useAuthStore.setState({ expiresAt: Date.now() + 60_000 })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    useAuthStore.getState().logout()
    queryClient.clear()
  })

  it('shows a viewer the document sections and the statement as a claim, with no write or withdraw', async () => {
    stubApi()
    renderFor('charter', false)
    expect(await screen.findByRole('region', { name: 'Statements by a person' })).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(4)
    expect(screen.queryByRole('heading', { name: 'Risk appetite and project-level risks' })).not.toBeInTheDocument()
    const budget = row('Budget envelope')
    const claim = within(budget).getByTestId('person-statement')
    expect(claim).toHaveClass('border-dashed')
    expect(claim).toHaveTextContent('Claim by sponsor@example.test (Owner)')
    expect(claim).toHaveTextContent('Forty thousand over six months.')
    expect(within(row('Sponsor')).getByText(/Not filled yet/)).toBeInTheDocument()
    expect(screen.getByText(/Only an Owner or a Manager can write/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Write|Edit|Withdraw/ })).not.toBeInTheDocument()
    expect(screen.getByRole('img', { name: '1 of 4 sections filled' })).toBeInTheDocument()
  })

  it('renders nothing for a document without person sections', async () => {
    const fetch = stubApi()
    const { container } = renderFor('wbs', true)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
    expect(container).toBeEmptyDOMElement()
  })

  it('lets a manager write a section: only the text is sent, then the list and the pack are read again', async () => {
    const fetch = stubApi({ [`PUT ${BASE}/charter.sponsor`]: () => response(200, statement({ id: 8, section: 'charter.sponsor', text: 'The board.' })) })
    renderFor('charter', true)
    await screen.findByRole('region', { name: 'Statements by a person' })
    const sponsor = row('Sponsor')
    expect(within(sponsor).queryByRole('button', { name: 'Withdraw' })).not.toBeInTheDocument()
    fireEvent.click(within(sponsor).getByRole('button', { name: 'Write' }))
    const save = within(sponsor).getByRole('button', { name: 'Save' })
    expect(save).toBeDisabled()
    const box = within(sponsor).getByRole('textbox', { name: 'Statement for Sponsor' })
    expect(box).toHaveAttribute('maxLength', '2000')
    fireEvent.change(box, { target: { value: 'The board.' } })
    expect(within(sponsor).getByText('10 / 2000 characters')).toBeInTheDocument()
    fireEvent.click(save)
    expect(await within(sponsor).findByText(/compile again to include it/)).toBeInTheDocument()
    const put = fetch.mock.calls.find(([, init]) => (init as RequestInit)?.method === 'PUT')!
    expect(JSON.parse(String((put[1] as RequestInit).body))).toEqual({ text: 'The board.' })
    await waitFor(() => expect(fetch.mock.calls.filter(([url]) => new URL(url as string).pathname === BASE).length).toBe(2))
  })

  it('opens the editor on the current text and shows a refusal', async () => {
    stubApi({ [`PUT ${BASE}/charter.budget`]: () => response(403, { detail: 'Requires Manager' }) })
    renderFor('charter', true)
    await screen.findByRole('region', { name: 'Statements by a person' })
    const budget = row('Budget envelope')
    fireEvent.click(within(budget).getByRole('button', { name: 'Edit' }))
    expect(within(budget).getByRole('textbox')).toHaveValue('Forty thousand over six months.')
    fireEvent.click(within(budget).getByRole('button', { name: 'Save' }))
    expect(await within(budget).findByRole('alert')).toHaveTextContent('Could not save the statement')
  })

  it('withdraws only after a confirmation', async () => {
    const fetch = stubApi({ [`DELETE ${BASE}/charter.budget`]: () => response(200, statement({ id: 9, text: null, withdrawn: true })) })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true)
    renderFor('charter', true)
    await screen.findByRole('region', { name: 'Statements by a person' })
    const withdraw = within(row('Budget envelope')).getByRole('button', { name: 'Withdraw' })
    fireEvent.click(withdraw)
    expect(fetch.mock.calls.some(([, init]) => (init as RequestInit)?.method === 'DELETE')).toBe(false)
    fireEvent.click(withdraw)
    expect(confirm).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => (init as RequestInit)?.method === 'DELETE')).toBe(true))
  })

  it('shows the history: who, in which role, when and which text, withdrawals included', async () => {
    stubApi({
      [`GET ${BASE}/charter.budget/history`]: () => response(200, {
        section: 'charter.budget',
        revisions: [
          statement({ id: 9, text: null, withdrawn: true, author: 'pm@example.test', author_role: 'Manager', supersedes_id: 7 }),
          statement(),
        ],
      }),
    })
    renderFor('charter', false)
    await screen.findByRole('region', { name: 'Statements by a person' })
    const budget = row('Budget envelope')
    fireEvent.click(within(budget).getByRole('button', { name: 'History' }))
    const list = await within(budget).findByRole('list', { name: 'Revisions, newest first' })
    const items = within(list).getAllByTestId('person-statement')
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent('Withdrawn by pm@example.test (Manager)')
    expect(items[0]).toHaveTextContent('The statement was withdrawn.')
    expect(items[1]).toHaveTextContent('Forty thousand over six months.')
    expect(items[1].querySelector('time')).toHaveAttribute('dateTime', '2026-11-16T09:30:00')
  })
})
