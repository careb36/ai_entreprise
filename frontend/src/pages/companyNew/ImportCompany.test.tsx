import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ImportCompany } from './ImportCompany'
import { queryClient } from '../../lib/queryClient'
import { api, type CompanyImportResult } from '../../services/api'

const EXPORT = {
  manifest: { format: 'la-fabrique.company/1', sha256: 'abc' },
  content: { company: { company_name: 'Acme' }, agents: [], routines: [] },
  required_secrets: [],
}

const RESULT: CompanyImportResult = {
  company_id: 42,
  company_name: 'Acme copy',
  created: { agents: 3, business_units: 1, routines: 2 },
  skipped: [{ what: 'company.stack_profile', why: 'set by the Owner, never from a file' }],
  to_set_up: [
    { provider_connection: 'main', provider: 'deepseek', base_url: 'https://api.example.test' },
    { kind: 'environment_variable', name: 'DATABASE_URL', for: 'sandbox' },
  ],
  routines_off: true,
  sha256: 'def',
  digest_matches: false,
}

function wrap(node: React.ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>)
}

function choose(content: string, name = 'acme.json') {
  const file = new File([content], name, { type: 'application/json' })
  fireEvent.change(screen.getByLabelText('Export file (.json)'), { target: { files: [file] } })
}

describe('starting a company from an exported file', () => {
  beforeEach(() => queryClient.clear())
  afterEach(() => {
    queryClient.clear()
    vi.restoreAllMocks()
  })

  it('sends the file with the chosen name and shows what is left to set up', async () => {
    const importCompany = vi.spyOn(api, 'importCompany').mockResolvedValue(RESULT)
    const onOpen = vi.fn()
    wrap(<ImportCompany onOpen={onOpen} />)

    fireEvent.click(screen.getByRole('button', { name: /Start from an exported file/ }))
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled()
    choose(JSON.stringify(EXPORT))

    const name = await screen.findByLabelText('Name of the new company')
    expect(name).toHaveValue('Acme')
    fireEvent.change(name, { target: { value: 'Acme copy' } })
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))

    await waitFor(() => expect(importCompany).toHaveBeenCalledWith(EXPORT, 'Acme copy'))
    expect(await screen.findByText(/Acme copy was created with 3 agents, 1 business units and 2 routines/)).toBeInTheDocument()
    expect(screen.getByText(/routines arrived turned off/)).toBeInTheDocument()
    expect(screen.getByText(/was edited after the export/)).toBeInTheDocument()
    expect(screen.getByText(/Provider connection main \(deepseek\)/)).toBeInTheDocument()
    expect(screen.getByText('Environment variable DATABASE_URL for the sandbox.')).toBeInTheDocument()
    expect(screen.getByText('company.stack_profile')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Open the company' }))
    expect(onOpen).toHaveBeenCalledWith(42, 'Acme copy')
  })

  it('refuses a file that is not an export before sending anything', async () => {
    const importCompany = vi.spyOn(api, 'importCompany')
    wrap(<ImportCompany onOpen={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Start from an exported file/ }))

    choose('not json')
    expect(await screen.findByRole('alert')).toHaveTextContent('not valid JSON')

    choose(JSON.stringify({ manifest: {} }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('not a company export'))
    expect(screen.getByRole('button', { name: 'Import' })).toBeDisabled()
    expect(importCompany).not.toHaveBeenCalled()
  })

  it("shows the server's reason when the import is refused", async () => {
    vi.spyOn(api, 'importCompany').mockRejectedValue(new Error('unknown export format'))
    wrap(<ImportCompany onOpen={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Start from an exported file/ }))
    choose(JSON.stringify(EXPORT))
    await screen.findByLabelText('Name of the new company')
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('The import failed: unknown export format')
  })
})
