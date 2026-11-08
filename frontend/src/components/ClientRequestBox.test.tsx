import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ClientRequestBox } from './ClientRequestBox'
import { useDraftSpecification } from '../services/specifications'

vi.mock('../services/specifications', () => ({ useDraftSpecification: vi.fn() }))

const mutate = vi.fn()
const mockDraft = (state: Record<string, unknown> = {}) =>
  vi.mocked(useDraftSpecification).mockReturnValue({ mutate, isPending: false, isError: false, error: null, data: undefined, ...state } as never)

function renderBox() {
  return render(<MemoryRouter><ClientRequestBox companyId={7} /></MemoryRouter>)
}

describe('ClientRequestBox', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockDraft()
  })

  it('sends what the client wrote, trimmed, to the PO for a draft', () => {
    renderBox()
    const send = screen.getByRole('button', { name: 'Ask the PO for a draft' })
    expect(send).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Your request, in plain words'), { target: { value: '  Export invoices as a spreadsheet.  ' } })
    fireEvent.click(send)
    expect(mutate).toHaveBeenCalledWith({ company_id: 7, brief: 'Export invoices as a spreadsheet.' })
  })

  it('links to the draft specification the client approves', () => {
    mockDraft({ data: { id: 5, title: 'Invoice export' } })
    renderBox()
    expect(screen.getByRole('link', { name: 'Read and approve the draft: Invoice export' })).toHaveAttribute('href', '/company/specifications/5')
  })

  it("says while the PO writes, and shows the backend's refusal", () => {
    mockDraft({ isPending: true })
    const { unmount } = renderBox()
    expect(screen.getByRole('button', { name: 'The PO is writing the draft…' })).toBeDisabled()
    unmount()
    mockDraft({ isError: true, error: new Error('No PO in this company') })
    renderBox()
    expect(screen.getByRole('alert')).toHaveTextContent('Could not draft it: No PO in this company')
  })
})
