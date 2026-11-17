/**
 * A count in a small pill, in tabular figures: how many rows a panel, a column
 * or a link holds. With `total`, it reads `shown/total` while a filter hides
 * some. The pill is an image with a spoken name, so `label` says in words
 * what the number counts ("3 tasks", "2 of 5 tasks shown").
 */
export function CountPill({ count, total, label, className = '' }: {
  count: number
  total?: number
  label: string
  className?: string
}) {
  const partial = total !== undefined && total !== count
  return (
    <span role="img" aria-label={label} title={label} className={`count-pill ${className}`}>
      {partial ? `${count}/${total}` : count}
    </span>
  )
}
