import { Link } from 'react-router-dom'
import type { AgentMessage } from '../../store/agentStore'
import { taskPath } from './identity'
import { formatTime } from './format'
import type { MessageGroup } from './types'
import { MessageContent } from './MessageContent'
import { AgentAvatar, CopyBtn, PreviewCard, StateBadge } from './atoms'
import { groupState, workingKeys, type ActiveAgent } from './liveStatus'

// ── Message row / group ───────────────────────────────────────────────────────

function MessageRow({ msg }: { msg: AgentMessage }) {
  const isError = msg.type === 'error'
  return (
    <div className="group/msg flex items-start gap-2 relative hover:bg-surface-800/30 rounded px-1 -mx-1 py-0.5 transition-colors">
      <span className="opacity-0 group-hover/msg:opacity-100 text-[10px] text-gray-600 min-w-[3rem] text-right pt-0.5 transition-opacity select-none shrink-0">
        {formatTime(msg.timestamp)}
      </span>
      <div className="flex-1 min-w-0">
        <MessageContent text={msg.content} isError={isError} />
        {msg.preview && <PreviewCard preview={msg.preview} task={msg.task} />}
      </div>
      <CopyBtn text={msg.content} />
    </div>
  )
}

export function SlackMessageGroup({ group, sprintId, activeAgents = [] }: {
  group: MessageGroup; sprintId: string; activeAgents?: ActiveAgent[]
}) {
  const { identity, messages } = group
  const firstMsg = messages[0]
  // The badge says where the group ended up (approved, delivered, refused…), not how it began.
  const state = groupState(messages, workingKeys(activeAgents))
  const isError  = state === 'error'

  return (
    <div className={`flex gap-3 px-4 py-1.5 animate-fade-in transition-colors ${
      isError ? 'bg-red-950/20 border-l-2 border-l-red-800/50' : 'hover:bg-surface-800/20'
    }`}>
      <div className="pt-0.5 shrink-0">
        <AgentAvatar identity={identity} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5 flex-wrap">
          <span className={`font-bold text-[13px] ${identity.color}`}>{identity.label}</span>
          {state && <StateBadge state={state} />}
          {firstMsg.task && firstMsg.taskId !== undefined && sprintId ? (
            <Link
              to={taskPath(sprintId, firstMsg.taskId)}
              className="text-[11px] text-gray-400 truncate max-w-[180px] hover:text-brand-300 hover:underline"
              title={firstMsg.task}
            >
              {firstMsg.task}
            </Link>
          ) : firstMsg.task && (
            <span className="text-[11px] text-gray-500 truncate max-w-[180px]" title={firstMsg.task}>
              {firstMsg.task}
            </span>
          )}
          <span className="text-[11px] text-gray-600 ml-auto shrink-0">
            {formatTime(firstMsg.timestamp)}
          </span>
        </div>
        {messages.map(msg => <MessageRow key={msg.id} msg={msg} />)}
      </div>
    </div>
  )
}
