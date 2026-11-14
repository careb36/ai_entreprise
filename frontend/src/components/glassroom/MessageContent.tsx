function renderInlineMarkdown(text: string): Array<string | JSX.Element> {
  const parts: Array<string | JSX.Element> = []
  const pattern = /\*\*(.+?)\*\*/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index))
    }
    parts.push(
      <strong key={`bold-${match.index}`} className="font-semibold text-gray-100">
        {match[1]}
      </strong>,
    )
    lastIndex = match.index + match[0].length
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }

  return parts.length > 0 ? parts : [text]
}

export function MessageContent({ text, isError }: { text: string; isError: boolean }) {
  return (
    <div className={`text-[13px] break-words leading-relaxed ${isError ? 'text-red-300' : 'text-gray-200'}`}>
      {text.split('\n').map((line, index) => (
        <p key={`${index}-${line.slice(0, 12)}`} className={index === 0 ? '' : 'mt-1'}>
          {line.length > 0 ? renderInlineMarkdown(line) : <span>&nbsp;</span>}
        </p>
      ))}
    </div>
  )
}
