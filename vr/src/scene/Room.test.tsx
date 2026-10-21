import ReactThreeTestRenderer from '@react-three/test-renderer'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { demoWorld } from '../demo'
import { reduce, type World } from '../model'
import { AGENTS_SHOWN, INBOX_RUNS_SHOWN, Room } from './Room'

// The labels are troika text, which needs a GPU and a font: here each one is a
// mesh that carries its text. The pinch handler is kept on the group too.
vi.mock('@react-three/drei', () => ({
  Grid: () => null,
  Text: ({ children }: { children: ReactNode }) => <mesh name="label" userData={{ text: String(children) }} />,
}))
vi.mock('@react-three/xr', () => ({
  Interactive: ({ children, onSelect }: { children: ReactNode; onSelect: () => void }) => <group name="pinchable" userData={{ onSelect }}>{children}</group>,
  useXR: () => false,
}))

type Scene = Awaited<ReturnType<typeof ReactThreeTestRenderer.create>>['scene']

const labels = (scene: Scene) => scene.findAll(node => node.props.name === 'label').map(node => String(node.props.userData.text))

/** A pinch on the card whose label reads `text`: the handler the Interactive wrapper holds. */
async function pinch(scene: Scene, text: string) {
  const card = scene.findAll(node => node.props.name === 'pinchable').find(node => node.findAll(child => child.props.name === 'label' && child.props.userData.text === text).length > 0)
  if (!card) throw new Error(`no card reads "${text}"`)
  await ReactThreeTestRenderer.act(async () => { (card.props.userData.onSelect as () => void)() })
}

async function render(world: World, extra: Partial<Parameters<typeof Room>[0]> = {}, eyes?: [number, number, number]) {
  const dispatch = vi.fn()
  // `eyes` puts the camera there looking straight ahead (down -Z), as a seated person would.
  const options = eyes ? { camera: { position: eyes, rotation: [0, 0, 0] as [number, number, number] } } : {}
  const renderer = await ReactThreeTestRenderer.create(<Room world={world} dispatch={dispatch} person="Ana" {...extra} />, options)
  return { renderer, dispatch }
}

describe('the room', () => {
  it('shows the demo company: the board by column, the agents, the log, the inbox and the hint', async () => {
    const { renderer } = await render(demoWorld())
    const texts = labels(renderer.scene)
    expect(texts).toContain('Board')
    expect(texts).toContain('Acme Payments · Sprint 3 — Checkout')
    expect(texts).toContain('Inbox · 4 waiting for you')
    expect(texts).toEqual(expect.arrayContaining(['To do · 2', 'In progress · 1', 'In review · 1', 'Done · 1', 'Blocked · 1']))
    expect(texts).toContain('Create testing strategy document')
    expect(texts).toContain('Developer · active')
    expect(texts).toContain('Tasks blocked for a person · 1')
    expect(texts).toContain('Runs awaiting a decision · 3')
    expect(texts).toContain('pr-agent · pr-102 · Rewrite the refund job')
    expect(texts).toContain('Pinch a card to pick it up; pinch it again to put it down.')
    expect(texts.some(text => text.endsWith('more'))).toBe(false)
    expect(texts.some(text => text.includes('fps'))).toBe(false)
  })

  it('picks a run up with a pinch, shows the evidence beside the suggestion, and the decision cards', async () => {
    const { renderer, dispatch } = await render(demoWorld())
    await pinch(renderer.scene, 'pr-agent · pr-102 · Rewrite the refund job')
    expect(dispatch).toHaveBeenCalledWith({ type: 'select', selection: { kind: 'run', id: 102 } })

    const picked = reduce(demoWorld(), { type: 'select', selection: { kind: 'run', id: 102 } })
    await renderer.update(<Room world={picked} dispatch={dispatch} person="Ana" />)
    const texts = labels(renderer.scene)
    expect(texts).toContain('Verifier: refuted')
    expect(texts.some(text => text.startsWith('Reviewer agent suggests rejected (a claim):'))).toBe(true)
    expect(texts).toEqual(expect.arrayContaining(['Approve', 'Reject', 'Hear the Reviewer agent']))

    await pinch(renderer.scene, 'Approve')
    expect(dispatch).toHaveBeenCalledWith({ type: 'decide_run', id: 102, decision: 'approved', by: 'Ana' })
  })

  it('offers to send a blocked task back, but not a frozen one', async () => {
    const blocked = reduce(demoWorld(), { type: 'select', selection: { kind: 'task', id: 36 } })
    const { renderer, dispatch } = await render(blocked)
    expect(labels(renderer.scene)).toContain('Send back to work')
    await pinch(renderer.scene, 'Send back to work')
    expect(dispatch).toHaveBeenCalledWith({ type: 'send_back', id: 36, by: 'Ana' })

    const frozen: World = { ...blocked, tasks: blocked.tasks.map(task => (task.id === 36 ? { ...task, frozen: true } : task)) }
    await renderer.update(<Room world={frozen} dispatch={dispatch} person="Ana" />)
    const texts = labels(renderer.scene)
    expect(texts).not.toContain('Send back to work')
    expect(texts).toContain('Frozen by an operator: it is decided on the web app, not from here.')
    expect(texts).toContain('Hear why it is blocked')
  })

  it('caps the agents and the inbox with "+n more"', async () => {
    const world = demoWorld()
    for (let index = 0; index < 4; index += 1) {
      world.agents.push({ role: `Helper ${index}`, state: 'idle', activity: 'Waiting' })
      world.runs.push({ id: 200 + index, producer: 'pr-agent', externalRef: `pr-${200 + index}`, verdict: 'ingested', observations: [] })
    }
    const { renderer } = await render(world)
    const texts = labels(renderer.scene)
    expect(texts).toContain(`+${world.agents.length - AGENTS_SHOWN} more`)
    expect(texts).toContain(`+${world.runs.length - INBOX_RUNS_SHOWN} more`)
    expect(texts.filter(text => text.startsWith('Helper'))).toHaveLength(AGENTS_SHOWN - 6)
  })

  it('says so once everything is decided, and where new work will show up', async () => {
    let world = demoWorld()
    for (const run of [101, 102, 103]) world = reduce(world, { type: 'decide_run', id: run, decision: 'approved', by: 'Ana' })
    world = reduce(world, { type: 'send_back', id: 36, by: 'Ana' })
    const { renderer } = await render(world)
    const texts = labels(renderer.scene)
    expect(texts).toContain('Inbox · nothing waits for you')
    expect(texts).toContain('Everything is decided. The company keeps working; what needs a person shows up here.')
    expect(texts).toContain('That was the last one.')
    expect(texts.some(text => text.startsWith('pr-agent ·'))).toBe(false)

    // A new run from the stream brings the inbox back.
    const again = reduce(world, { type: 'run', run: { id: 104, producer: 'pr-agent', externalRef: 'pr-104', verdict: 'verified', observations: [] } })
    await renderer.update(<Room world={again} dispatch={vi.fn()} person="Ana" />)
    expect(labels(renderer.scene)).toContain('Inbox · 1 waiting for you')
    expect(labels(renderer.scene)).not.toContain('That was the last one.')
  })

  it('shows the frame rate only when asked', async () => {
    const { renderer } = await render(demoWorld(), { stats: true })
    expect(labels(renderer.scene)).toContain('fps …')
    // Forty frames of a sixtieth of a second: the first window closes at 60 fps.
    await ReactThreeTestRenderer.act(async () => { await renderer.advanceFrames(40, 1 / 60) })
    expect(labels(renderer.scene)).toContain('60 fps')
  })

  it('picks the card under the gaze once it is held, only when the eyes-only pick is on', async () => {
    // Eyes level with the Developer's card on the centre panel, looking straight at it.
    const eyes: [number, number, number] = [0.38, 1.475, 0]
    const { renderer, dispatch } = await render(demoWorld(), {}, eyes)
    expect(renderer.scene.findAll(node => node.props.name === 'reticle')).toHaveLength(0)
    await ReactThreeTestRenderer.act(async () => { await renderer.advanceFrames(120, 1 / 60) })
    expect(dispatch).not.toHaveBeenCalled()

    const gazing = await render(demoWorld(), { gaze: true }, eyes)
    expect(gazing.renderer.scene.findAll(node => node.props.name === 'reticle')).toHaveLength(1)
    // Nothing renders here, so the world matrices the raycast reads are refreshed by hand.
    gazing.renderer.scene.instance.updateMatrixWorld(true)
    // A second's look: held, not yet picked.
    await ReactThreeTestRenderer.act(async () => { await gazing.renderer.advanceFrames(60, 1 / 60) })
    expect(gazing.dispatch).not.toHaveBeenCalled()
    // A second and a half: picked, once.
    await ReactThreeTestRenderer.act(async () => { await gazing.renderer.advanceFrames(120, 1 / 60) })
    expect(gazing.dispatch).toHaveBeenCalledTimes(1)
    expect(gazing.dispatch).toHaveBeenCalledWith({ type: 'select', selection: { kind: 'agent', role: 'Developer' } })
  })

  it('reads the Reviewer agent aloud with the browser voice', async () => {
    const speak = vi.fn()
    const cancel = vi.fn()
    vi.stubGlobal('speechSynthesis', { speak, cancel })
    vi.stubGlobal('SpeechSynthesisUtterance', class { text: string; lang = ''; rate = 1; constructor(text: string) { this.text = text } })
    const picked = reduce(demoWorld(), { type: 'select', selection: { kind: 'run', id: 102 } })
    const { renderer } = await render(picked)
    await pinch(renderer.scene, 'Hear the Reviewer agent')
    expect(speak).toHaveBeenCalledTimes(1)
    expect((speak.mock.calls[0][0] as { text: string }).text).toContain('The Reviewer agent suggests rejecting this run.')
    vi.unstubAllGlobals()
  })
})

