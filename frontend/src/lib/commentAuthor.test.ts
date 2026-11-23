import { describe, expect, it } from 'vitest'
import i18n from '../i18n'
import { commentAuthor } from './commentAuthor'

const t = i18n.getFixedT('en')

describe("a comment's author", () => {
  it("shows a person's recorded name, never the bare 'user'", () => {
    expect(commentAuthor({ author: 'user', by: 'Ada Owner', identified: true }, t)).toBe('Ada Owner')
  })

  it('says so when no signed-in person made the change', () => {
    expect(commentAuthor({ author: 'user', by: null, identified: false }, t)).toBe('Unidentified (no signed-in person)')
  })

  it('says the name was not recorded on an older comment', () => {
    expect(commentAuthor({ author: 'user' }, t)).toBe('A person (name not recorded)')
  })

  it('shows a role, a gate or the system as written', () => {
    expect(commentAuthor({ author: 'QA' }, t)).toBe('QA')
    expect(commentAuthor({ author: 'System', by: 'ignored' }, t)).toBe('System')
  })
})
