import i18n from '../../i18n'
import type { BacklogItem } from '../../services/api'
import type { Translate } from '../../i18n/translate'

// ── Helpers ───────────────────────────────────────────────────────────────────

// Memoized date formatters to avoid repeated Intl.DateTimeFormat instantiation
const _timeFormatter = new Intl.DateTimeFormat('en', { hour: '2-digit', minute: '2-digit', hour12: true })
const _dateFormatters = new Map<string, Intl.DateTimeFormat>()

// Module-level cache for reference date strings to avoid re-instantiating
// new Date() on every formatDate call (high-frequency render path).
let _todayStr = new Date().toDateString()
let _yesterdayStr = new Date(Date.now() - 86400000).toDateString()

// Periodic update to handle day rollovers (every 60s).
if (typeof window !== 'undefined') {
  setInterval(() => {
    const now = new Date()
    _todayStr = now.toDateString()
    _yesterdayStr = new Date(now.getTime() - 86400000).toDateString()
  }, 60000)
}

function _getDateFormatter(lang: string): Intl.DateTimeFormat {
  if (!_dateFormatters.has(lang)) {
    _dateFormatters.set(lang, new Intl.DateTimeFormat(lang || 'en', { weekday: 'long', month: 'long', day: 'numeric' }))
  }
  return _dateFormatters.get(lang)!
}

export function formatTime(iso: string): string {
  try {
    return _timeFormatter.format(new Date(iso))
  } catch { return '' }
}

export function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    const dStr = d.toDateString()

    if (dStr === _todayStr) return i18n.t('glassroom.today', 'Today')
    if (dStr === _yesterdayStr) return i18n.t('glassroom.yesterday', 'Yesterday')

    return _getDateFormatter(i18n.language || 'en').format(d)
  } catch { return '' }
}

export function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000))
  if (totalSeconds < 60) return `${totalSeconds}s`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`
}

export function formatAgeShort(ms: number): string {
  if (ms < 1000) return '0s'
  return formatElapsed(ms)
}

export function parseCooldownUntil(detail?: string): string | undefined {
  if (!detail) return undefined
  const match = detail.match(/cooldown_until=([^\s]+)/)
  return match?.[1]
}

export function hasDeliverableComment(comment: { author: string; text: string; created_at: string }): boolean {
  const text = (comment.text || '').trim()
  if (!text || comment.author === 'System') return false
  if (text.toUpperCase().startsWith('APPROVED:') || text.toUpperCase().startsWith('NEEDS WORK:')) return false
  return true
}

export function extractBlockedReason(comments: BacklogItem['comments'] | undefined): string | undefined {
  const entries = comments || []
  for (let index = entries.length - 1; index >= 0; index -= 1) {
    const text = (entries[index]?.text || '').trim()
    if (!text) continue
    const lowered = text.toLowerCase()
    if (
      lowered.includes('manual triage') ||
      lowered.includes('quality gate failed') ||
      lowered.includes('agent error:') ||
      lowered.includes('missing deliverable') ||
      lowered.includes('freeze after')
    ) {
      return text
    }
  }
  return undefined
}

export function phaseLabel(t: Translate, phase?: string): string {
  const labels: Record<string, string> = {
    queued: 'Queued',
    claimed: 'Claimed',
    context: 'Context',
    memory_retrieved: 'Memory retrieved',
    execution: 'Execution',
    deliverable: 'Deliverable',
    artifact_generation: 'Artifacts',
    qa_handoff: 'QA handoff',
    qa_review: 'QA review',
    qa_approved: 'Approved',
    rework: 'Rework',
    retry: 'Retry',
    restart_recovery: 'Restart recovery',
    blocked_recovery: 'Blocked recovery',
    quality_gate: 'Quality gate',
    qa_gate: 'QA gate',
    implementation_complete: 'Implementation complete',
    kickoff_complete: 'Kickoff complete',
    architect_consult: 'Architect consult',
    todo_recovery: 'Todo recovery',
    unknown: 'Unknown',
  }
  const key = phase || 'unknown'
  return t(`glassroom.phaseLabels.${key}`, { defaultValue: labels[key] ?? phase ?? 'Unknown' })
}

export function getEventTypes(t: Translate) {
  return [
    { value: '', label: t('glassroom.eventTypes.all', { defaultValue: 'All types' }) },
    { value: 'message', label: t('glassroom.eventTypes.message', { defaultValue: 'Message' }) },
    { value: 'agent_start', label: t('glassroom.eventTypes.working', { defaultValue: 'Working' }) },
    { value: 'agent_complete', label: t('glassroom.eventTypes.delivered', { defaultValue: 'Delivered' }) },
    { value: 'status', label: t('glassroom.eventTypes.status', { defaultValue: 'Status' }) },
    { value: 'error', label: t('glassroom.eventTypes.error', { defaultValue: 'Error' }) },
  ] as const
}
