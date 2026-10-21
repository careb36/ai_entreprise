import { useEffect, useRef, useState, type ComponentProps, type Dispatch, type ReactNode } from 'react'
import { useFrame } from '@react-three/fiber'
import { Grid, Text } from '@react-three/drei'
import { Interactive, useXR } from '@react-three/xr'
import { Vector3, type Group } from 'three'
import { FrameRate } from '../fps'
import { approach, hintTarget, hintVisible, type Vec3 } from '../hint'
import { arc, fit, grid, type Placement } from '../layout'
import { speak } from '../speech'
import {
  COLUMNS,
  blockedForPerson,
  pendingRuns,
  selectedAgent,
  selectedRun,
  selectedTask,
  tasksByStatus,
  type Action,
  type Agent,
  type Run,
  type Task,
  type TaskStatus,
  type World,
} from '../model'

// The font ships with the room (vr/public/fonts), so no label depends on a CDN
// in the headset; the glyphs beyond ASCII the room uses are listed so the
// font is loaded once, up front.
const FONT_URL = `${import.meta.env.BASE_URL}fonts/Roboto-Regular.ttf`
const CHARACTERS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .,:;!?()[]{}+-*/=_#%&\'"`<>@·…—›¿¡áéíóúñÁÉÍÓÚÑ'

function Label(props: ComponentProps<typeof Text>) {
  return <Text font={FONT_URL} characters={CHARACTERS} {...props} />
}

const INK = '#f3f4f6'
const MUTED = '#9ca3af'
const PANEL = '#111827'
const CARD = '#1f2937'
const HOVER = '#374151'
const SELECTED = '#2563eb'

const STATUS_COLOR: Record<TaskStatus, string> = {
  todo: '#4b5563',
  in_progress: '#2563eb',
  in_review: '#7c3aed',
  done: '#059669',
  blocked: '#dc2626',
}

const AGENT_COLOR: Record<Agent['state'], string> = {
  idle: '#4b5563',
  active: '#059669',
  waiting: '#d97706',
  error: '#dc2626',
}

const VERDICT_COLOR: Record<Run['verdict'], string> = {
  verified: '#059669',
  refuted: '#dc2626',
  unverifiable: '#d97706',
  ingested: '#4b5563',
}

interface RoomProps {
  world: World
  dispatch: Dispatch<Action>
  /** Who the pinches are recorded as. */
  person: string
  /** Show the frame rate in the room (`?stats=1`), for the Quest pass. */
  stats?: boolean
}

/**
 * The room: three panels on an arc in front of a seated person (the board,
 * the company at work, the inbox) and a detail panel below the middle one
 * for whatever card was picked up. Everything is a pinch away: a card
 * selects, the action cards decide.
 */
export function Room({ world, dispatch, person, stats = false }: RoomProps) {
  const [board, centre, inbox] = arc(3, { radius: 1.5, spread: Math.PI * 0.62, height: 1.25 })
  const waiting = pendingRuns(world).length + blockedForPerson(world).length
  // The first pinch of the session puts the entry hint away.
  const [pinched, setPinched] = useState(false)
  const act = (action: Action) => {
    setPinched(true)
    dispatch(action)
  }
  const pick = (selection: NonNullable<World['selected']>) => act({ type: 'select', selection })

  return (
    <>
      {/* The panels and cards are flat (basic materials): the lights are for the hands. */}
      <ambientLight intensity={0.9} />
      <directionalLight position={[1, 3, 2]} intensity={0.7} />
      <Grid infiniteGrid fadeDistance={10} cellColor="#1f2937" sectionColor="#374151" position={[0, 0, 0]} />

      <Panel placement={board} title="Board" width={1.3} height={0.95}>
        <Board tasks={world.tasks} selected={world.selected} onPick={pick} />
      </Panel>

      <Panel placement={centre} title={`${world.company} · ${world.sprint}`} width={1.2} height={0.95}>
        <Agents agents={world.agents} selected={world.selected} onPick={pick} />
        <Log world={world} />
      </Panel>

      <Panel placement={inbox} title={waiting ? `Inbox · ${waiting} waiting for you` : 'Inbox · nothing waits for you'} width={1.2} height={0.95}>
        <Inbox world={world} onPick={pick} />
      </Panel>

      <Detail world={world} dispatch={act} person={person} />
      <Hint pinched={pinched} />
      {stats && <FrameRateLabel />}
    </>
  )
}

function Panel({ placement, title, width, height, children }: {
  placement: Placement
  title: string
  width: number
  height: number
  children: ReactNode
}) {
  return (
    <group position={placement.position} rotation={[0, placement.rotationY, 0]}>
      <mesh>
        <planeGeometry args={[width, height]} />
        <meshBasicMaterial color={PANEL} transparent opacity={0.92} />
      </mesh>
      <Label position={[-width / 2 + 0.03, height / 2 - 0.045, 0.002]} fontSize={0.045} anchorX="left" anchorY="middle" color={INK} maxWidth={width - 0.06}>
        {title}
      </Label>
      <group position={[0, -0.04, 0]}>{children}</group>
    </group>
  )
}

interface CardProps {
  x: number
  y: number
  width?: number
  height?: number
  label: string
  sub?: string
  labelSize?: number
  subSize?: number
  color?: string
  accent?: string
  selected?: boolean
  onPick: () => void
}

const PAD = 0.025

/**
 * A card a person can pinch (in VR) or click (on a desktop). The label
 * wraps from the top and the sub line sits at the bottom; both are clipped
 * to the card, so a long title never spills over the next card.
 */
export function Card({ x, y, width = 0.5, height = 0.12, label, sub, labelSize = 0.03, subSize = 0.022, color = CARD, accent, selected = false, onPick }: CardProps) {
  const [hover, setHover] = useState(false)
  const fill = selected ? SELECTED : hover ? HOVER : color
  // A card lifts off its panel when a hand is on it, and further once picked up.
  const lift = selected ? 0.03 : hover ? 0.015 : 0.004
  const textWidth = width - PAD * 2
  const subHeight = sub ? subSize * 1.4 : 0
  const labelHeight = height - 0.03 - subHeight
  return (
    <Interactive onSelect={onPick} onHover={() => setHover(true)} onBlur={() => setHover(false)}>
      <group position={[x, y, lift]}>
        <mesh onClick={onPick} onPointerOver={() => setHover(true)} onPointerOut={() => setHover(false)}>
          <planeGeometry args={[width, height]} />
          <meshBasicMaterial color={fill} />
        </mesh>
        {accent && (
          <mesh position={[-width / 2 + 0.006, 0, 0.001]}>
            <planeGeometry args={[0.012, height]} />
            <meshBasicMaterial color={accent} />
          </mesh>
        )}
        <Label
          position={[-width / 2 + PAD, sub ? height / 2 - 0.015 : labelSize * 0.6, 0.002]}
          fontSize={labelSize}
          lineHeight={1.1}
          anchorX="left"
          anchorY="top"
          color={INK}
          maxWidth={textWidth}
          clipRect={[0, -labelHeight, textWidth, 0]}
        >
          {label}
        </Label>
        {sub && (
          <Label
            position={[-width / 2 + PAD, -height / 2 + 0.014, 0.002]}
            fontSize={subSize}
            anchorX="left"
            anchorY="bottom"
            color={MUTED}
            maxWidth={textWidth * 4}
            clipRect={[0, 0, textWidth, subSize * 1.3]}
          >
            {sub}
          </Label>
        )}
      </group>
    </Interactive>
  )
}

function isSelected(selected: World['selected'], candidate: World['selected']): boolean {
  if (!selected || !candidate || selected.kind !== candidate.kind) return false
  return selected.kind === 'agent' ? selected.role === (candidate as { role: string }).role : selected.id === (candidate as { id: number }).id
}

/** Cards a board column shows before "+n more": what fits a panel at arm's length. */
export const CARDS_PER_COLUMN = 5

function Board({ tasks, selected, onPick }: { tasks: Task[]; selected: World['selected']; onPick: (s: NonNullable<World['selected']>) => void }) {
  const columns = tasksByStatus(tasks)
  const columnWidth = 0.25
  const cardHeight = 0.13
  const left = -((COLUMNS.length - 1) * columnWidth) / 2
  return (
    <>
      {COLUMNS.map((column, index) => {
        const x = left + index * columnWidth
        const cards = columns[column.status]
        const shown = cards.slice(0, CARDS_PER_COLUMN)
        const slots = grid(shown.length, { cols: 1, width: 0.23, height: cardHeight, gap: 0.015 })
        return (
          <group key={column.status} position={[x, 0, 0]}>
            <Label position={[-0.115, 0.36, 0.002]} fontSize={0.026} anchorX="left" anchorY="middle" color={STATUS_COLOR[column.status]} maxWidth={0.23}>
              {`${column.label} · ${cards.length}`}
            </Label>
            {shown.map((task, slot) => (
              <Card
                key={task.id}
                x={slots[slot][0]}
                y={0.26 - (slots[0][1] - slots[slot][1])}
                width={0.23}
                height={cardHeight}
                label={task.title}
                labelSize={0.023}
                sub={task.assignee ? `${task.assignee}${task.rework ? ` · ${task.rework} rework` : ''}` : undefined}
                subSize={0.018}
                accent={STATUS_COLOR[task.status]}
                selected={isSelected(selected, { kind: 'task', id: task.id })}
                onPick={() => onPick({ kind: 'task', id: task.id })}
              />
            ))}
            {cards.length > CARDS_PER_COLUMN && (
              <Label position={[-0.115, 0.26 - shown.length * (cardHeight + 0.015) + 0.02, 0.002]} fontSize={0.02} anchorX="left" anchorY="middle" color={MUTED}>
                {`+${cards.length - CARDS_PER_COLUMN} more`}
              </Label>
            )}
          </group>
        )
      })}
    </>
  )
}

/** Agent cards shown on the centre panel: three rows of three, then "+n more". */
export const AGENTS_SHOWN = 9

function Agents({ agents, selected, onPick }: { agents: Agent[]; selected: World['selected']; onPick: (s: NonNullable<World['selected']>) => void }) {
  const shown = agents.slice(0, AGENTS_SHOWN)
  const slots = grid(shown.length, { cols: 3, width: 0.36, height: 0.11, gap: 0.02 })
  const top = shown.length ? slots[0][1] : 0
  return (
    <>
      <Label position={[-0.56, 0.36, 0.002]} fontSize={0.028} anchorX="left" anchorY="middle" color={MUTED}>
        The company at work
      </Label>
      {agents.length > AGENTS_SHOWN && (
        <Label position={[0.56, 0.36, 0.002]} fontSize={0.022} anchorX="right" anchorY="middle" color={MUTED}>
          {`+${agents.length - AGENTS_SHOWN} more`}
        </Label>
      )}
      {shown.map((agent, index) => (
        <Card
          key={agent.role}
          x={slots[index][0]}
          y={0.265 - (top - slots[index][1])}
          width={0.36}
          height={0.11}
          label={`${agent.role} · ${agent.state}`}
          labelSize={0.026}
          sub={agent.activity}
          subSize={0.019}
          accent={AGENT_COLOR[agent.state]}
          selected={isSelected(selected, { kind: 'agent', role: agent.role })}
          onPick={() => onPick({ kind: 'agent', role: agent.role })}
        />
      ))}
    </>
  )
}

function Log({ world }: { world: World }) {
  const lines = world.log.slice(-6)
  return (
    <>
      <Label position={[-0.56, -0.08, 0.002]} fontSize={0.028} anchorX="left" anchorY="middle" color={MUTED}>
        What happened
      </Label>
      {lines.map((entry, index) => (
        <Label key={`${entry.at}-${index}`} position={[-0.56, -0.13 - index * 0.045, 0.002]} fontSize={0.024} anchorX="left" anchorY="middle" color={INK} maxWidth={1.1}>
          {fit(entry.text, 70)}
        </Label>
      ))}
    </>
  )
}

/** Cards the inbox shows of each kind before "+n more": what fits beside the detail panel. */
export const INBOX_BLOCKED_SHOWN = 2
export const INBOX_RUNS_SHOWN = 4

function Inbox({ world, onPick }: { world: World; onPick: (s: NonNullable<World['selected']>) => void }) {
  const allBlocked = blockedForPerson(world)
  const allRuns = pendingRuns(world)
  const blocked = allBlocked.slice(0, INBOX_BLOCKED_SHOWN)
  const runs = allRuns.slice(0, INBOX_RUNS_SHOWN)
  const blockedSlots = grid(blocked.length, { cols: 1, width: 1.1, height: 0.1, gap: 0.015 })
  const runSlots = grid(runs.length, { cols: 1, width: 1.1, height: 0.1, gap: 0.015 })
  const runsTop = 0.36 - 0.06 - blocked.length * 0.115 - 0.08 - (allBlocked.length > blocked.length ? 0.04 : 0)
  if (allBlocked.length + allRuns.length === 0) {
    // The payoff of the last decision: the inbox says so, and where new work will show up.
    return (
      <>
        <Label position={[-0.56, 0.36, 0.002]} fontSize={0.028} anchorX="left" anchorY="middle" color={MUTED}>
          Tasks blocked for a person · 0
        </Label>
        <Label position={[-0.56, 0.26, 0.002]} fontSize={0.028} anchorX="left" anchorY="middle" color={MUTED}>
          Runs awaiting a decision · 0
        </Label>
        <Label position={[0, 0.02, 0.002]} fontSize={0.036} anchorX="center" anchorY="middle" color={INK} maxWidth={1.0} textAlign="center" lineHeight={1.2}>
          Everything is decided. The company keeps working; what needs a person shows up here.
        </Label>
      </>
    )
  }
  return (
    <>
      <Label position={[-0.56, 0.36, 0.002]} fontSize={0.028} anchorX="left" anchorY="middle" color={MUTED}>
        {`Tasks blocked for a person · ${allBlocked.length}`}
      </Label>
      {allBlocked.length > blocked.length && (
        <Label position={[-0.56, runsTop + 0.05, 0.002]} fontSize={0.022} anchorX="left" anchorY="middle" color={MUTED}>
          {`+${allBlocked.length - blocked.length} more`}
        </Label>
      )}
      {blocked.map((task, index) => (
        <Card
          key={task.id}
          x={0}
          y={0.27 - (blockedSlots[0][1] - blockedSlots[index][1])}
          width={1.1}
          height={0.1}
          label={fit(task.title, 48)}
          sub={fit(task.reason ?? '', 70)}
          accent={STATUS_COLOR.blocked}
          selected={isSelected(world.selected, { kind: 'task', id: task.id })}
          onPick={() => onPick({ kind: 'task', id: task.id })}
        />
      ))}
      <Label position={[-0.56, runsTop, 0.002]} fontSize={0.028} anchorX="left" anchorY="middle" color={MUTED}>
        {`Runs awaiting a decision · ${allRuns.length}`}
      </Label>
      {allRuns.length > runs.length && (
        <Label position={[0.56, runsTop, 0.002]} fontSize={0.022} anchorX="right" anchorY="middle" color={MUTED}>
          {`+${allRuns.length - runs.length} more`}
        </Label>
      )}
      {runs.map((run, index) => (
        <Card
          key={run.id}
          x={0}
          y={runsTop - 0.09 - (runSlots[0][1] - runSlots[index][1])}
          width={1.1}
          height={0.1}
          label={fit(`${run.producer} · ${run.externalRef}`, 48)}
          sub={run.suggestion ? `${run.verdict} · Reviewer agent suggests ${run.suggestion.decision}` : `${run.verdict} · no suggestion`}
          accent={VERDICT_COLOR[run.verdict]}
          selected={isSelected(world.selected, { kind: 'run', id: run.id })}
          onPick={() => onPick({ kind: 'run', id: run.id })}
        />
      ))}
    </>
  )
}

const DETAIL: Placement = { position: [0, 0.62, -1.15], rotationY: 0 }

/** What was picked up, with the actions a person can take on it. */
function Detail({ world, dispatch, person }: RoomProps) {
  const task = selectedTask(world)
  const run = selectedRun(world)
  const agent = selectedAgent(world)
  const width = 1.2
  const height = 0.42
  const line = (index: number) => 0.12 - index * 0.05
  const lines: { text: string; color?: string }[] = []
  let actions: { label: string; color: string; act: () => void }[] = []

  if (run) {
    lines.push({ text: fit(`${run.producer} · ${run.externalRef}`, 60) })
    lines.push({ text: `Verifier: ${run.verdict}`, color: VERDICT_COLOR[run.verdict] })
    for (const observation of run.observations.slice(0, 3)) lines.push({ text: fit(observation, 72), color: MUTED })
    if (run.suggestion) {
      lines.push({ text: `Reviewer agent suggests ${run.suggestion.decision} (a claim): ${fit(run.suggestion.reason, 60)}`, color: '#93c5fd' })
    } else {
      lines.push({ text: 'The Reviewer agent made no suggestion; it is yours to decide.', color: MUTED })
    }
    if (run.decision) {
      lines.push({ text: `${run.decision.by} ${run.decision.decision} it.` })
    } else {
      actions = [
        { label: 'Approve', color: '#059669', act: () => dispatch({ type: 'decide_run', id: run.id, decision: 'approved', by: person }) },
        { label: 'Reject', color: '#dc2626', act: () => dispatch({ type: 'decide_run', id: run.id, decision: 'rejected', by: person }) },
      ]
    }
    if (run.suggestion) {
      const suggestion = run.suggestion
      // The reason read aloud, so the eyes can stay on the observations.
      actions.push({ label: 'Hear the Reviewer agent', color: '#1d4ed8', act: () => { speak(`The Reviewer agent suggests ${suggestion.decision === 'approved' ? 'approving' : 'rejecting'} this run. ${suggestion.reason}`) } })
    }
  } else if (task) {
    lines.push({ text: fit(task.title, 60) })
    lines.push({ text: `${COLUMNS.find(column => column.status === task.status)?.label ?? task.status}${task.assignee ? ` · ${task.assignee}` : ''} · ${task.rework} rework`, color: STATUS_COLOR[task.status] })
    if (task.reason) lines.push({ text: fit(task.reason, 72), color: MUTED })
    if (task.status === 'blocked') {
      const reason = task.reason
      if (task.frozen) {
        lines.push({ text: 'Frozen by an operator: it is decided on the web app, not from here.', color: MUTED })
      } else {
        actions = [{ label: 'Send back to work', color: '#2563eb', act: () => dispatch({ type: 'send_back', id: task.id, by: person }) }]
      }
      if (reason) actions.push({ label: 'Hear why it is blocked', color: '#1d4ed8', act: () => { speak(`${task.title} is blocked. ${reason}`) } })
    }
  } else if (agent) {
    lines.push({ text: agent.role })
    lines.push({ text: `${agent.state} · ${fit(agent.activity, 60)}`, color: AGENT_COLOR[agent.state] })
  } else if (pendingRuns(world).length + blockedForPerson(world).length === 0 && world.log.some(entry => / (approved|rejected|sent) /.test(entry.text))) {
    lines.push({ text: 'That was the last one.', color: INK })
    lines.push({ text: 'The company keeps working; the inbox fills again when something needs you.', color: MUTED })
  } else {
    lines.push({ text: 'Pinch a card to pick it up; pinch it again to put it down.', color: MUTED })
    lines.push({ text: 'Suggestions and verdicts are shown beside each other: your pinch decides.', color: MUTED })
  }

  return (
    <group position={DETAIL.position} rotation={[-0.25, DETAIL.rotationY, 0]}>
      <mesh>
        <planeGeometry args={[width, height]} />
        <meshBasicMaterial color={PANEL} transparent opacity={0.92} />
      </mesh>
      {lines.map((entry, index) => (
        <Label key={index} position={[-width / 2 + 0.03, line(index), 0.002]} fontSize={index === 0 ? 0.036 : 0.026} anchorX="left" anchorY="middle" color={entry.color ?? INK} maxWidth={width - 0.06}>
          {entry.text}
        </Label>
      ))}
      {actions.map((action, index) => (
        <Card
          key={action.label}
          x={-width / 2 + 0.03 + 0.17 + index * 0.37}
          y={-height / 2 + 0.07}
          width={0.34}
          height={0.08}
          label={action.label}
          color={action.color}
          onPick={action.act}
        />
      ))}
    </group>
  )
}

const HINT_WIDTH = 0.56
const HINT_HEIGHT = 0.14

/**
 * The entry hint: for the first seconds of a headset session, a small panel
 * trails the head (a lazy follow, never glued to the eyes) and says what a
 * pinch does. It goes with the first pinch, or on its own. Outside a
 * session the page's own header says the same.
 */
function Hint({ pinched }: { pinched: boolean }) {
  const presenting = useXR(state => state.isPresenting)
  const [enteredAt, setEnteredAt] = useState<number | null>(null)
  const [visible, setVisible] = useState(false)
  const group = useRef<Group>(null)
  const placed = useRef(false)
  const head = useRef(new Vector3())
  const gaze = useRef(new Vector3())

  useEffect(() => {
    setEnteredAt(presenting ? Date.now() : null)
    placed.current = false
  }, [presenting])

  useFrame(({ camera }) => {
    const show = hintVisible({ enteredAt, pinched }, Date.now())
    if (show !== visible) setVisible(show)
    if (!show || !group.current) return
    camera.getWorldPosition(head.current)
    camera.getWorldDirection(gaze.current)
    const target = hintTarget(head.current.toArray() as Vec3, gaze.current.toArray() as Vec3)
    const current = group.current.position.toArray() as Vec3
    const next = placed.current ? approach(current, target, 0.08) : target
    placed.current = true
    group.current.position.set(next[0], next[1], next[2])
    group.current.lookAt(head.current)
  })

  if (!visible) return null
  return (
    <group ref={group}>
      <mesh>
        <planeGeometry args={[HINT_WIDTH, HINT_HEIGHT]} />
        <meshBasicMaterial color={PANEL} transparent opacity={0.9} />
      </mesh>
      <Label position={[0, 0.025, 0.002]} fontSize={0.03} anchorX="center" anchorY="middle" color={INK} maxWidth={HINT_WIDTH - 0.04} textAlign="center">
        Pinch a card to pick it up; pinch it again to put it down.
      </Label>
      <Label position={[0, -0.03, 0.002]} fontSize={0.022} anchorX="center" anchorY="middle" color={MUTED} maxWidth={HINT_WIDTH - 0.04} textAlign="center">
        Your pinch decides; the suggestions are claims.
      </Label>
    </group>
  )
}

/** The frame rate, under the detail panel, for the pass on the headset (`?stats=1`). */
function FrameRateLabel() {
  const counter = useRef(new FrameRate())
  // The frames' own deltas, summed: the renderer's clock, not the wall's.
  const elapsed = useRef(0)
  const [rate, setRate] = useState<number | null>(null)
  useFrame((_, delta) => {
    elapsed.current += delta * 1000
    const next = counter.current.tick(elapsed.current)
    if (next !== null) setRate(next)
  })
  return (
    <Label position={[DETAIL.position[0] + 0.57, DETAIL.position[1] - 0.26, DETAIL.position[2]]} fontSize={0.03} anchorX="right" anchorY="middle" color={rate !== null && rate < 60 ? '#f59e0b' : MUTED}>
      {rate === null ? 'fps …' : `${rate} fps`}
    </Label>
  )
}
