/**
 * A documentation pack's Markdown, rendered safely. The text comes from
 * records agents wrote, so it is untrusted even after the backend escaped it:
 * raw HTML is dropped (`skipHtml`), images are not rendered (no request leaves
 * the page because a document said so), and a link keeps its target only when
 * it is an in-page anchor or an http(s) address, opened without an opener.
 * GFM (`remark-gfm`) gives the registers their tables.
 */
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** The href a pack's link may keep: `#anchor` or http(s); anything else is dropped. */
export function safeHref(url: string): string {
  const trimmed = url.trim()
  if (trimmed.startsWith('#')) return trimmed
  try {
    const parsed = new URL(trimmed)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : ''
  } catch {
    return ''
  }
}

const components: Components = {
  a: ({ href, children }) => {
    const target = href ? safeHref(href) : ''
    if (!target) return <span>{children}</span>
    return target.startsWith('#')
      ? <a href={target}>{children}</a>
      : <a href={target} target="_blank" rel="noopener noreferrer nofollow">{children}</a>
  },
  table: ({ children }) => <div className="overflow-x-auto"><table>{children}</table></div>,
}

export function PackMarkdown({ markdown }: { markdown: string }) {
  return (
    <div data-testid="pack-markdown"
      className="prose prose-invert prose-sm max-w-[72ch] break-words leading-relaxed prose-headings:font-semibold prose-headings:tracking-tight prose-headings:text-gray-50 prose-h1:text-xl prose-h2:mt-8 prose-h2:border-b prose-h2:border-surface-700 prose-h2:pb-1.5 prose-h2:text-base prose-h3:text-sm prose-p:text-gray-300 prose-li:text-gray-300 prose-a:text-brand-300 prose-strong:text-gray-100 prose-code:text-gray-200 prose-th:text-gray-200 prose-td:text-gray-300 prose-table:text-xs">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        disallowedElements={['img']}
        urlTransform={safeHref}
        components={components}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  )
}

export default PackMarkdown
