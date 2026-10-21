import { useState, type Dispatch, type ReactNode } from 'react'
import { Grid, Text } from '@react-three/drei'
import { Interactive } from '@react-three/xr'
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
}

/**
 * The room: three panels on an arc in front of a seated person (the board,
 * the company at work, the inbox) and a detail panel below the middle one
 * for whatever card was picked up. Everything is a pinch away: a card
 * selects, the action cards decide.
 */
export function Room({ world, dispatch, person }: RoomProps) {
  const [board, centre, inbox] = arc(3, { radius: 1.5, spread: Math.PI * 0.62, height: 1.25 })
  const waiting = pendingRuns(world).length + blockedForPerson(world).length
  const pick = (selection: NonNullable<World['selected']>) => dispatch({ type: 'select', selection })

  return (
    <>
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

      <Panel placement={inbox} title={`Inbox · ${waiting} waiting for you`} width={1.2} height={0.95}>
        <Inbox world={world} onPick={pick} />
      </Panel>

      <Detail world={world} dispatch={dispatch} person={person} />
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
        <meshStandardMaterial color={PANEL} transparent opacity={0.92} />
      </mesh>
      <Text position={[-width / 2 + 0.03, height / 2 - 0.045, 0.002]} fontSize={0.045} anchorX="left" anchorY="middle" color={INK} maxWidth={width - 0.06}>
        {title}
      </Text>
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
          <meshStandardMaterial color={fill} />
        </mesh>
        {accent && (
          <mesh position={[-width / 2 + 0.006, 0, 0.001]}>
            <planeGeometry args={[0.012, height]} />
            <meshStandardMaterial color={accent} />
          </mesh>
        )}
        <Text
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
        </Text>
        {sub && (
          <Text
            position={[-width / 2 + PAD, -height / 2 + 0.014, 0.002]}
            fontSize={subSize}
            anchorX="left"
            anchorY="bottom"
            color={MUTED}
            maxWidth={textWidth * 4}
            clipRect={[0, 0, textWidth, subSize * 1.3]}
          >
            {sub}
          </Text>
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
            <Text position={[-0.115, 0.36, 0.002]} fontSize={0.026} anchorX="left" anchorY="middle" color={STATUS_COLOR[column.status]} maxWidth={0.23}>
              {`${column.label} · ${cards.length}`}
            </Text>
            {shown.map((task, slot) => (
              <Card
                key={task.id}
                x={slots[slot][0]}
                y={0.26 - (slots[0][1] - slots[slot][1])}
                width={0.23}
                height={cardHeight}
                label={task.title}
                labelSize={0.021}
                sub={task.assignee ? `${task.assignee}${task.rework ? ` · ${task.rework} rework` : ''}` : undefined}
                subSize={0.017}
                accent={STATUS_COLOR[task.status]}
                selected={isSelected(selected, { kind: 'task', id: task.id })}
                onPick={() => onPick({ kind: 'task', id: task.id })}
              />
            ))}
            {cards.length > CARDS_PER_COLUMN && (
              <Text position={[-0.115, 0.26 - shown.length * (cardHeight + 0.015) + 0.02, 0.002]} fontSize={0.02} anchorX="left" anchorY="middle" color={MUTED}>
                {`+${cards.length - CARDS_PER_COLUMN} more`}
              </Text>
            )}
          </group>
        )
      })}
    </>
  )
}

function Agents({ agents, selected, onPick }: { agents: Agent[]; selected: World['selected']; onPick: (s: NonNullable<World['selected']>) => void }) {
  const slots = grid(agents.length, { cols: 3, width: 0.36, height: 0.11, gap: 0.02 })
  return (
    <>
      <Text position={[-0.56, 0.36, 0.002]} fontSize={0.028} anchorX="left" anchorY="middle" color={MUTED}>
        The company at work
      </Text>
      {agents.map((agent, index) => (
        <Card
          key={agent.role}
          x={slots[index][0]}
          y={0.2 + slots[index][1]}
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
      <Text position={[-0.56, -0.08, 0.002]} fontSize={0.028} anchorX="left" anchorY="middle" color={MUTED}>
        What happened
      </Text>
      {lines.map((entry, index) => (
        <Text key={`${entry.at}-${index}`} position={[-0.56, -0.13 - index * 0.045, 0.002]} fontSize={0.024} anchorX="left" anchorY="middle" color={INK} maxWidth={1.1}>
          {fit(entry.text, 70)}
        </Text>
      ))}
    </>
  )
}

function Inbox({ world, onPick }: { world: World; onPick: (s: NonNullable<World['selected']>) => void }) {
  const blocked = blockedForPerson(world)
  const runs = pendingRuns(world)
  const blockedSlots = grid(blocked.length, { cols: 1, width: 1.1, height: 0.1, gap: 0.015 })
  const runSlots = grid(runs.length, { cols: 1, width: 1.1, height: 0.1, gap: 0.015 })
  const runsTop = 0.36 - 0.06 - blocked.length * 0.115 - 0.08
  return (
    <>
      <Text position={[-0.56, 0.36, 0.002]} fontSize={0.028} anchorX="left" anchorY="middle" color={MUTED}>
        {`Tasks blocked for a person · ${blocked.length}`}
      </Text>
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
      <Text position={[-0.56, runsTop, 0.002]} fontSize={0.028} anchorX="left" anchorY="middle" color={MUTED}>
        {`Runs awaiting a decision · ${runs.length}`}
      </Text>
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
      actions = [{ label: 'Send back to work', color: '#2563eb', act: () => dispatch({ type: 'send_back', id: task.id, by: person }) }]
      if (reason) actions.push({ label: 'Hear why it is blocked', color: '#1d4ed8', act: () => { speak(`${task.title} is blocked. ${reason}`) } })
    }
  } else if (agent) {
    lines.push({ text: agent.role })
    lines.push({ text: `${agent.state} · ${fit(agent.activity, 60)}`, color: AGENT_COLOR[agent.state] })
  } else {
    lines.push({ text: 'Pinch a card to pick it up; pinch it again to put it down.', color: MUTED })
    lines.push({ text: 'Suggestions and verdicts are shown beside each other: your pinch decides.', color: MUTED })
  }

  return (
    <group position={DETAIL.position} rotation={[-0.25, DETAIL.rotationY, 0]}>
      <mesh>
        <planeGeometry args={[width, height]} />
        <meshStandardMaterial color={PANEL} transparent opacity={0.92} />
      </mesh>
      {lines.map((entry, index) => (
        <Text key={index} position={[-width / 2 + 0.03, line(index), 0.002]} fontSize={index === 0 ? 0.036 : 0.026} anchorX="left" anchorY="middle" color={entry.color ?? INK} maxWidth={width - 0.06}>
          {entry.text}
        </Text>
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
