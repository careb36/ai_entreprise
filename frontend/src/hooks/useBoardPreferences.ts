import { useCallback, useEffect, useState } from 'react'

/** How dense the board's cards are: every detail, or the title and the signals only. */
export type BoardDensity = 'comfortable' | 'compact'

interface Preferences {
  collapsed: string[]
  density: BoardDensity
}

const DEFAULTS: Preferences = { collapsed: [], density: 'comfortable' }

function storageKey(sprintId: number | null): string {
  return `fabrique-board:${sprintId ?? 'none'}`
}

function read(sprintId: number | null): Preferences {
  try {
    const raw = localStorage.getItem(storageKey(sprintId))
    if (!raw) return DEFAULTS
    const parsed = JSON.parse(raw) as Partial<Preferences>
    return {
      collapsed: Array.isArray(parsed.collapsed) ? parsed.collapsed.filter(c => typeof c === 'string') : [],
      density: parsed.density === 'compact' ? 'compact' : 'comfortable',
    }
  } catch {
    return DEFAULTS
  }
}

/**
 * This viewer's layout of a sprint's board: which columns are folded and how
 * dense the cards are. A per-browser convenience, kept in localStorage when it
 * can be; the board works the same without it.
 */
export function useBoardPreferences(sprintId: number | null) {
  const [prefs, setPrefs] = useState<Preferences>(() => read(sprintId))

  useEffect(() => { setPrefs(read(sprintId)) }, [sprintId])

  const update = useCallback((next: (current: Preferences) => Preferences) => {
    setPrefs(current => {
      const value = next(current)
      try {
        localStorage.setItem(storageKey(sprintId), JSON.stringify(value))
      } catch {
        // Storage blocked or full: the layout still changes for this visit.
      }
      return value
    })
  }, [sprintId])

  const toggleColumn = useCallback((id: string) => update(current => ({
    ...current,
    collapsed: current.collapsed.includes(id) ? current.collapsed.filter(c => c !== id) : [...current.collapsed, id],
  })), [update])

  const setAllCollapsed = useCallback((ids: string[]) => update(current => ({ ...current, collapsed: ids })), [update])

  const setDensity = useCallback((density: BoardDensity) => update(current => ({ ...current, density })), [update])

  return { collapsed: prefs.collapsed, density: prefs.density, toggleColumn, setAllCollapsed, setDensity }
}
