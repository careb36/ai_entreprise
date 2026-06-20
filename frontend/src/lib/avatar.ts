/**
 * Avatar utilities shared by App.tsx (UserMenu) and ProfilePage.
 *
 * `getInitials` derives up to two uppercase letters from a display name or
 * email address:
 *   - Display name: first letter of each word (up to 2 words), e.g. "Jane Doe" → "JD"
 *   - Email fallback: first two characters, e.g. "jane@…" → "JA"
 */
export function getInitials(name: string | undefined, email: string): string {
  const trimmed = name?.trim()
  if (trimmed) {
    return trimmed
      .split(' ')
      .filter((w) => w.length > 0)
      .map((w) => w[0])
      .join('')
      .slice(0, 2)
      .toUpperCase()
  }
  return email.slice(0, 2).toUpperCase()
}
