import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../i18n'
import { normalizeSSEEvent } from './agentEvents'

// The backend sends a fixed Glassroom message as a key and its parameters
// beside the English text; the live view shows it in the interface language.
const recovered = {
  type: 'status',
  semantic_type: 'task.recovered',
  role: 'System',
  task: 'Add bench baseline',
  message: '♻️ Recovered: no live worker held Add bench baseline; returned to todo.',
  message_key: 'glassroom.events.recovered',
  message_params: { task: 'Add bench baseline' },
}

function content(raw: Record<string, unknown>): string {
  const event = normalizeSSEEvent(raw)
  return event && 'content' in event ? String(event.content) : ''
}

describe('Glassroom messages in the interface language', () => {
  afterEach(async () => {
    await i18n.changeLanguage('en')
  })

  it('shows a keyed message in Spanish when the interface is in Spanish', async () => {
    await i18n.changeLanguage('es')
    expect(content(recovered)).toBe(
      '♻️ Recuperada: ningún worker sostenía Add bench baseline; vuelve a Por hacer para correr de nuevo.',
    )
  })

  it('shows it in English when the interface is in English', async () => {
    await i18n.changeLanguage('en')
    expect(content(recovered)).toContain('Recovered: no live worker held Add bench baseline')
  })

  it('falls back to the English text for a key this build does not know', async () => {
    await i18n.changeLanguage('es')
    expect(content({ ...recovered, message_key: 'glassroom.events.unknown_kind' })).toBe(recovered.message)
  })

  it('leaves an agent message without a key as written', async () => {
    await i18n.changeLanguage('es')
    expect(content({ type: 'message', role: 'Dev', message: 'Texto del agente.' })).toBe('Texto del agente.')
  })
})
