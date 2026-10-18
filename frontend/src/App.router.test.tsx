import { render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { expect, it, vi } from 'vitest'
import App from './App'

it('does not emit React Router future-flag warnings on the first router render', () => {
  window.history.replaceState(null, '', '/login')
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  try {
    render(<QueryClientProvider client={client}><App /></QueryClientProvider>)
    expect(warn.mock.calls.flat().join(' ')).not.toMatch(/React Router Future Flag Warning/)
  } finally {
    warn.mockRestore()
    client.clear()
  }
})
