/** A backend timestamp written without a zone is UTC: read it as such and show it in the viewer's local time. */
export function localTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const withZone = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`
  const date = new Date(withZone)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}
