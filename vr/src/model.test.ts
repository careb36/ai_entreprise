import { describe, expect, it } from 'vitest'
import { demoWorld } from './demo'
import { LOG_LIMIT, blockedForPerson, pendingRuns, reduce, selectedRun, selectedTask, tasksByStatus, type World } from './model'

describe('the room\'s model', () => {
  it('groups the tasks by column and lists what waits for a person', () => {
    const world = demoWorld()
    const columns = tasksByStatus(world.tasks)
    expect(columns.todo.map(task => task.id)).toEqual([34, 35])
    expect(columns.blocked.map(task => task.title)).toEqual(['Create testing strategy document'])
    expect(blockedForPerson(world).map(task => task.id)).toEqual([36])
    expect(pendingRuns(world).map(run => run.id)).toEqual([101, 102, 103])
  })

  it('selects a card with a pinch and puts it down with another', () => {
    let world = reduce(demoWorld(), { type: 'select', selection: { kind: 'run', id: 102 } })
    expect(selectedRun(world)?.externalRef).toContain('pr-102')
    world = reduce(world, { type: 'select', selection: { kind: 'task', id: 36 } })
    expect(selectedTask(world)?.id).toBe(36)
    world = reduce(world, { type: 'select', selection: { kind: 'task', id: 36 } })
    expect(world.selected).toBeNull()
  })

  it('records a person\'s decision on a run, beside the suggestion, and says whether they agreed', () => {
    let world = reduce(demoWorld(), { type: 'select', selection: { kind: 'run', id: 102 } })
    world = reduce(world, { type: 'decide_run', id: 102, decision: 'approved', by: 'Ana' })

    const run = world.runs.find(candidate => candidate.id === 102)!
    expect(run.decision).toEqual({ by: 'Ana', decision: 'approved' })
    // The suggestion stays as it was: a claim beside the decision.
    expect(run.suggestion?.decision).toBe('rejected')
    expect(pendingRuns(world).map(candidate => candidate.id)).toEqual([101, 103])
    expect(world.selected).toBeNull()
    expect(world.log.at(-1)?.text).toBe('Ana approved pr-102 · Rewrite the refund job, against the Reviewer agent\'s suggestion')

    world = reduce(world, { type: 'decide_run', id: 103, decision: 'rejected', by: 'Ana' })
    expect(world.log.at(-1)?.text).toBe('Ana rejected issue-57 · Add CSV export')
    // Decided once: a second pinch changes nothing.
    expect(reduce(world, { type: 'decide_run', id: 103, decision: 'approved', by: 'Ana' })).toBe(world)
  })

  it('sends a blocked task back to the top of the board with one more rework, and only a blocked one', () => {
    let world = reduce(demoWorld(), { type: 'send_back', id: 36, by: 'Ana' })
    const task = world.tasks.find(candidate => candidate.id === 36)!
    expect([task.status, task.rework, task.reason]).toEqual(['todo', 3, undefined])
    expect(blockedForPerson(world)).toEqual([])
    expect(world.log.at(-1)?.text).toBe('Ana sent "Create testing strategy document" back to work')

    const unchanged = reduce(world, { type: 'send_back', id: 33, by: 'Ana' })
    expect(unchanged).toBe(world)
    world = unchanged
  })

  it('keeps the last events only', () => {
    let world: World = { ...demoWorld(), log: [] }
    for (let index = 0; index < LOG_LIMIT + 5; index += 1) {
      world = reduce(world, { type: 'event', text: `event ${index}`, at: index })
    }
    expect(world.log).toHaveLength(LOG_LIMIT)
    expect(world.log[0].text).toBe('event 5')
  })

  it('updates a task or a run the board reports, or adds one it did not know', () => {
    let world = reduce(demoWorld(), { type: 'task', task: { id: 33, status: 'in_review', rework: 1 } })
    expect(world.tasks.find(task => task.id === 33)).toMatchObject({ title: 'Rate limit the checkout API', status: 'in_review', rework: 1 })
    world = reduce(world, { type: 'task', task: { id: 99, title: 'New one' } })
    expect(world.tasks.find(task => task.id === 99)).toEqual({ id: 99, title: 'New one', status: 'todo', rework: 0 })

    const run = { id: 104, producer: 'github', externalRef: 'pr-104', verdict: 'verified' as const, observations: [] }
    world = reduce(world, { type: 'run', run })
    expect(pendingRuns(world).map(candidate => candidate.id)).toEqual([101, 102, 103, 104])
    world = reduce(world, { type: 'run', run: { ...run, verdict: 'refuted' } })
    expect(world.runs.filter(candidate => candidate.id === 104).map(candidate => candidate.verdict)).toEqual(['refuted'])
  })

  it('loads a world from a backend over the demo', () => {
    const world = reduce(demoWorld(), { type: 'load', world: { mode: 'live', company: 'Real Co', tasks: [] } })
    expect([world.mode, world.company, world.tasks]).toEqual(['live', 'Real Co', []])
    expect(world.agents.length).toBeGreaterThan(0)
  })
})
