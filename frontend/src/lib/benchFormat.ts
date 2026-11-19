/**
 * Number formats for the bench's scorecards (`pages/Bench.tsx`): locale-aware,
 * no words, so the text stays in the translations.
 */
export const formatCount = (value: number, locale: string) =>
  new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(value)

export const formatShare = (value: number, locale: string) =>
  new Intl.NumberFormat(locale, { style: 'percent', maximumFractionDigits: 1 }).format(value)

export const formatRatio = (value: number, locale: string) =>
  new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value)

/** US cents as dollars, as the routing record's cost is kept. */
export const formatCents = (cents: number, locale: string) =>
  new Intl.NumberFormat(locale, { style: 'currency', currency: 'USD', maximumFractionDigits: 4 }).format(cents / 100)

/** `20261114T201804Z` as a date and time in UTC; the stamp itself if it is not one. */
export function formatStamp(stamp: string, locale: string) {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(stamp)
  if (!m) return stamp
  const date = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]))
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: 'UTC' }).format(date)
}
