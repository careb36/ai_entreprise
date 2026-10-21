import { afterEach, describe, expect, it, vi } from 'vitest'
import { speak } from './speech'

class FakeUtterance {
  lang = ''
  rate = 1
  constructor(public text: string) {}
}

describe('speaking a line', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('reads the text with the browser voice, interrupting what was being said', () => {
    vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance)
    const synth = { speak: vi.fn(), cancel: vi.fn() } as unknown as SpeechSynthesis

    expect(speak('The Reviewer agent suggests reject.', synth)).toBe(true)

    expect(synth.cancel).toHaveBeenCalledOnce()
    const utterance = vi.mocked(synth.speak).mock.calls[0][0] as unknown as FakeUtterance
    expect(utterance.text).toBe('The Reviewer agent suggests reject.')
    expect(utterance.lang).toBe('en-US')
  })

  it('stays silent, and says so, without speech synthesis or without words', () => {
    vi.stubGlobal('SpeechSynthesisUtterance', FakeUtterance)
    expect(speak('anything', undefined)).toBe(false)
    const synth = { speak: vi.fn(), cancel: vi.fn() } as unknown as SpeechSynthesis
    expect(speak('   ', synth)).toBe(false)
    expect(synth.speak).not.toHaveBeenCalled()
  })
})
