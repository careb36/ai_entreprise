import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SourcesConsulted } from './SourcesConsulted'
import '../i18n'

describe('SourcesConsulted', () => {
  it('renders nothing without sources', () => {
    const { container } = render(<SourcesConsulted sources={[]} headingId="s" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('lists a fetched page with its hash, a package lookup and a refused host', () => {
    render(
      <SourcesConsulted
        headingId="s"
        sources={[
          { tool: 'fetch', url: 'https://docs.pytest.org/en/stable/changelog.html', host: 'docs.pytest.org', allowed: true, status: 200, sha256: 'abcdef0123456789ff', fetched_at: '2026-11-07T10:00:00Z', is_error: false },
          { tool: 'package', url: 'https://pypi.org/pypi/fastapi/json', host: 'pypi.org', allowed: true, status: 200, sha256: '0000000000001111', package: 'fastapi', ecosystem: 'pypi', is_error: false },
          { tool: 'fetch', url: 'https://pastebin.example/x', host: 'pastebin.example', allowed: false, status: null, is_error: true },
        ]}
      />,
    )
    expect(screen.getByRole('heading', { name: /3 sources consulted/i })).toBeInTheDocument()
    const page = screen.getByRole('link', { name: 'https://docs.pytest.org/en/stable/changelog.html' })
    expect(page).toHaveAttribute('href', 'https://docs.pytest.org/en/stable/changelog.html')
    expect(screen.getByText(/200 · abcdef012345/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'pypi:fastapi' })).toBeInTheDocument()
    expect(screen.getByText(/refused: pastebin.example is not on the company's list/i)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'https://pastebin.example/x' })).toBeNull()
  })
})
