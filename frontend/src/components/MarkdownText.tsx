import ReactMarkdown from 'react-markdown'

/** Render markdown-ish text safely. Falls back to plain text on error. */
export function MarkdownText({ text, className }: { text: string; className?: string }) {
  return (
    <div className={className ?? 'prose prose-invert prose-sm max-w-none whitespace-pre-wrap break-words'}>
      <ReactMarkdown>{text}</ReactMarkdown>
    </div>
  )
}

export default MarkdownText