import ReactMarkdown from 'react-markdown'

const SAFE_URL = /^(https?:\/\/|mailto:|#)/i

/**
 * A URL agent text may link to: http(s), mailto or an anchor; anything else
 * (javascript:, data:, vbscript:, a relative path into the app) is dropped.
 */
export function safeWikiUrl(url: string): string {
  const trimmed = url.trim()
  return SAFE_URL.test(trimmed) ? trimmed : ''
}

/**
 * Markdown from a wiki page, which may be agent text and is untrusted: raw
 * HTML is skipped (never injected), images are not loaded (no tracking pixel
 * from a page), and links open apart, without a referrer, only to safe URLs.
 */
export function WikiMarkdown({ text, className }: { text: string; className?: string }) {
  const base = 'prose prose-invert prose-sm max-w-none break-words'
  return (
    <div className={className ? `${base} ${className}` : base}>
      <ReactMarkdown
        skipHtml
        disallowedElements={['img']}
        unwrapDisallowed
        urlTransform={safeWikiUrl}
        components={{
          a: ({ href, children }) =>
            href ? (
              <a href={href} target="_blank" rel="noopener noreferrer nofollow">{children}</a>
            ) : (
              <span>{children}</span>
            ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}

export default WikiMarkdown
