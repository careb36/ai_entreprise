import type { AgentMessage } from '../../store/agentStore'
import { getAgent } from './identity'
import { formatDate } from './format'
import type { MessageGroup } from './types'

export function groupMessages(messages: AgentMessage[]): { date: string; groups: MessageGroup[] }[] {
  const dayMap = new Map<string, AgentMessage[]>()
  for (const msg of messages) {
    const dateKey = msg.timestamp ? new Date(msg.timestamp).toDateString() : 'Unknown'
    if (!dayMap.has(dateKey)) dayMap.set(dateKey, [])
    dayMap.get(dateKey)!.push(msg)
  }

  const result: { date: string; groups: MessageGroup[] }[] = []
  for (const [, dayMsgs] of dayMap) {
    const groups: MessageGroup[] = []
    let current: MessageGroup | null = null
    for (const msg of dayMsgs) {
      const agentKey = msg.agent || msg.role || 'System'
      if (current && current.agent === agentKey) {
        current.messages.push(msg)
      } else {
        current = {
          agent: agentKey,
          identity: getAgent(agentKey),
          timestamp: msg.timestamp,
          messages: [msg],
        }
        groups.push(current)
      }
    }
    result.push({ date: formatDate(dayMsgs[0]?.timestamp || ''), groups })
  }
  return result
}
