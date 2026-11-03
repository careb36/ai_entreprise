/**
 * A file's unified-diff hunks as rows of a review table: each line with the
 * number it has in the old file and in the new one, as GitHub and GitLab
 * show them.
 */
export type PatchRowKind = 'hunk' | 'add' | 'del' | 'context' | 'note'

export interface PatchRow {
  kind: PatchRowKind
  oldLine: number | null
  newLine: number | null
  text: string
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/

export function patchRows(patch: string | null | undefined): PatchRow[] {
  const rows: PatchRow[] = []
  let oldLine = 0
  let newLine = 0
  for (const line of (patch ?? '').split('\n')) {
    const hunk = HUNK.exec(line)
    if (hunk) {
      oldLine = Number(hunk[1])
      newLine = Number(hunk[2])
      rows.push({ kind: 'hunk', oldLine: null, newLine: null, text: line })
    } else if (line.startsWith('+')) {
      rows.push({ kind: 'add', oldLine: null, newLine: newLine++, text: line.slice(1) })
    } else if (line.startsWith('-')) {
      rows.push({ kind: 'del', oldLine: oldLine++, newLine: null, text: line.slice(1) })
    } else if (line.startsWith('\\')) {
      // "\ No newline at end of file"
      rows.push({ kind: 'note', oldLine: null, newLine: null, text: line.slice(1).trim() })
    } else if (rows.length > 0) {
      rows.push({ kind: 'context', oldLine: oldLine++, newLine: newLine++, text: line.startsWith(' ') ? line.slice(1) : line })
    }
  }
  return rows
}
