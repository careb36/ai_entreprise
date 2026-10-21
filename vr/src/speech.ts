/**
 * Reads a line aloud with the browser's own voice, so a person can keep
 * their eyes on the evidence while the Reviewer agent's reason or a block's
 * cause is spoken. Returns false when the device has no speech synthesis;
 * the room then stays silent and the text is still on the panel.
 */
export function speak(text: string, synth: SpeechSynthesis | undefined = globalThis.speechSynthesis): boolean {
  if (!synth || typeof SpeechSynthesisUtterance === 'undefined' || !text.trim()) return false
  synth.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.lang = 'en-US'
  utterance.rate = 1.05
  synth.speak(utterance)
  return true
}
