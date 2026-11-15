import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { WikiMarkdown, safeWikiUrl } from './WikiMarkdown'

describe('WikiMarkdown', () => {
  it('never injects raw HTML from agent text', () => {
    const { container } = render(
      <WikiMarkdown text={'Hello <script>alert(1)</script> <b onclick="x()">bold</b>\n\n<iframe src="https://e.test"></iframe>'} />,
    )
    expect(container.querySelector('script')).toBeNull()
    expect(container.querySelector('iframe')).toBeNull()
    expect(container.querySelector('b')).toBeNull()
    expect(container).toHaveTextContent('Hello')
  })

  it('drops unsafe links and images, keeps safe links apart', () => {
    const { container } = render(
      <WikiMarkdown text={'[bad](javascript:alert(1)) [data](data:text/html,x) [ok](https://example.com) ![pixel](https://t.test/p.png)'} />,
    )
    expect(container.querySelector('img')).toBeNull()
    const ok = screen.getByRole('link', { name: 'ok' })
    expect(ok).toHaveAttribute('href', 'https://example.com')
    expect(ok).toHaveAttribute('rel', 'noopener noreferrer nofollow')
    expect(screen.queryByRole('link', { name: 'bad' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'data' })).toBeNull()
    expect(container).toHaveTextContent('bad')
  })

  it('accepts only http(s), mailto and anchors', () => {
    expect(safeWikiUrl('https://a.test')).toBe('https://a.test')
    expect(safeWikiUrl('#facts')).toBe('#facts')
    expect(safeWikiUrl(' JavaScript:alert(1)')).toBe('')
    expect(safeWikiUrl('/company/members')).toBe('')
  })
})
