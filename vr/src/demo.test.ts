import { describe, expect, it } from 'vitest'
import { demoTimeline, demoWorld } from './demo'
import { pendingRuns, reduce, tasksByStatus } from './model'

describe('the demo\'s first minute', () => {
  it('plays in order, within a minute, and leaves a coherent company', () => {
    const steps = demoTimeline()
    expect(steps.map(step => step.at)).toEqual([...steps.map(step => step.at)].sort((a, b) => a - b))
    expect(steps.at(-1)!.at).toBeLessThanOrEqual(60_000)

    let world = demoWorld()
    const before = world.log.length
    for (const step of steps) for (const action of step.actions) world = reduce(world, action)

    // The rate-limit task went to review, came back once, and went to review again.
    const task = world.tasks.find(candidate => candidate.id === 33)!
    expect([task.status, task.rework]).toEqual(['in_review', 1])
    expect(tasksByStatus(world.tasks).in_progress).toEqual([])
    // A new run arrived in the inbox with the Reviewer agent's suggestion beside it, undecided.
    expect(pendingRuns(world).map(run => run.id)).toEqual([101, 102, 103, 104])
    expect(world.runs.find(run => run.id === 104)?.decision).toBeUndefined()
    // Every step said what happened, and the agents' cards moved.
    expect(world.log.length).toBe(before + steps.length)
    expect(world.agents.find(agent => agent.role === 'QA')?.activity).toBe('Reviewing the rework')
    expect(world.agents).toHaveLength(demoWorld().agents.length)
  })
})
