import type { AgentMessage } from '../../store/agentStore'
import { authorKey, getAgent } from './identity'
import { formatDate } from './format'
import { afterClose } from './liveStatus'
import type { MessageGroup } from './types'

/**
 * The feed by day, each day in runs of one author's messages. *messages* are
 * in the order they happened (`insertByTime`). With *closedAt*, a run never
 * spans the sprint's close: the first group after it is marked `afterClose`,
 * so the feed shows what came after the close as such.
 */
export function groupMessages(messages: AgentMessage[], closedAt?: string | null): { date: string; groups: MessageGroup[] }[] {
  const dayMap = new Map<string, AgentMessage[]>()
  for (const msg of messages) {
    const dateKey = msg.timestamp ? new Date(msg.timestamp).toDateString() : 'Unknown'
    if (!dayMap.has(dateKey)) dayMap.set(dateKey, [])
    dayMap.get(dateKey)!.push(msg)
  }

  const result: { date: string; groups: MessageGroup[] }[] = []
  let closedSeen = false
  for (const [, dayMsgs] of dayMap) {
    const groups: MessageGroup[] = []
    let current: MessageGroup | null = null
    for (const msg of dayMsgs) {
      const identity = getAgent(authorKey(msg))
      const crossesClose = !closedSeen && afterClose(msg, closedAt)
      if (current && current.agent === identity.key && !crossesClose) {
        current.messages.push(msg)
      } else {
        current = {
          agent: identity.key,
          identity,
          timestamp: msg.timestamp,
          messages: [msg],
          ...(crossesClose ? { afterClose: true } : {}),
        }
        groups.push(current)
      }
      if (crossesClose) closedSeen = true
    }
    result.push({ date: formatDate(dayMsgs[0]?.timestamp || ''), groups })
  }
  return result
}
