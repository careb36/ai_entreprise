import { describe, expect, it } from 'vitest'
import { patchRows } from './patch'

describe('patch rows', () => {
  it('numbers each line in the old file and the new one', () => {
    const rows = patchRows('@@ -3,3 +3,4 @@ def handler():\n a = 1\n-b = 2\n+b = 20\n+c = 3\n d = 4')
    expect(rows.map(r => [r.kind, r.oldLine, r.newLine, r.text])).toEqual([
      ['hunk', null, null, '@@ -3,3 +3,4 @@ def handler():'],
      ['context', 3, 3, 'a = 1'],
      ['del', 4, null, 'b = 2'],
      ['add', null, 4, 'b = 20'],
      ['add', null, 5, 'c = 3'],
      ['context', 5, 6, 'd = 4'],
    ])
  })

  it('starts again at each hunk and keeps git notes apart', () => {
    const rows = patchRows('@@ -1 +1 @@\n-x\n+y\n\\ No newline at end of file\n@@ -40,2 +40,2 @@\n k\n-l\n+m')
    expect(rows.filter(r => r.kind === 'note').map(r => r.text)).toEqual(['No newline at end of file'])
    expect(rows.find(r => r.text === 'k')).toMatchObject({ oldLine: 40, newLine: 40 })
    expect(rows.find(r => r.text === 'm')).toMatchObject({ newLine: 41 })
  })

  it('has no rows without a hunk', () => {
    expect(patchRows(null)).toEqual([])
    expect(patchRows('')).toEqual([])
    expect(patchRows('stray text')).toEqual([])
  })
})
