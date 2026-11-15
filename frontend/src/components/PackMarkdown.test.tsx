import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PackMarkdown, safeHref } from './PackMarkdown'
import { EVIL, pack } from '../test/documentationFixtures'

describe('PackMarkdown', () => {
  it('renders tables and drops raw HTML, images and script links', () => {
    const { container } = render(<PackMarkdown markdown={pack().documents[0].markdown} />)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[onerror]')).toBeNull()
    const links = [...container.querySelectorAll('a')]
    expect(links.every(a => !a.getAttribute('href')?.toLowerCase().startsWith('javascript'))).toBe(true)
    expect(screen.getByText('click').tagName).toBe('SPAN')
    expect(container.innerHTML).not.toContain('<script')
  })

  it('shows escaped record text as text', () => {
    render(<PackMarkdown markdown={`Name: ${EVIL.replace(/</g, '&lt;').replace(/>/g, '&gt;')}`} />)
    expect(screen.getByTestId('pack-markdown')).toHaveTextContent('<script>alert(2)</script>')
  })

  it('keeps only anchors and http(s) links', () => {
    expect(safeHref('#user-content-fn-1')).toBe('#user-content-fn-1')
    expect(safeHref('https://c4model.com')).toBe('https://c4model.com/')
    expect(safeHref('javascript:alert(1)')).toBe('')
    expect(safeHref(' JaVaScRiPt:alert(1)')).toBe('')
    expect(safeHref('data:text/html,<b>x</b>')).toBe('')
    expect(safeHref('/v1/secret')).toBe('')
  })
})
