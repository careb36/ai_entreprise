import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { Translate } from '../i18n/translate'
import { Inbox as InboxIcon, Send } from 'lucide-react'
import { errorDetail } from '../services/api'
import {
  INTAKE_CATEGORIES,
  INTAKE_STATUSES,
  useDecideIntake,
  useIntake,
  useIntakeRecord,
  useSendIntake,
  type IntakeDecision,
  type IntakeItem,
  type IntakeLink,
  type IntakeStatus,
} from '../services/intake'
import { useSpecifications } from '../services/specifications'
import { useSprints } from '../hooks/useQueries'

const OPEN_SPRINTS = ['active', 'planned', 'paused', 'needs_split']
type Choice = 'accept_task' | 'accept_specification' | 'decline' | 'duplicate' | 'snooze'
const CHOICES: Choice[] = ['accept_task', 'accept_specification', 'decline', 'duplicate', 'snooze']

/** Where a link goes in the app, or null for another request (shown as #id). */
function linkPath(link: IntakeLink): string | null {
  if (link.kind === 'task') return `/task/${link.id}`
  if (link.kind === 'specification') return `/company/specifications/${link.id}`
  return null
}

function LinkTo({ link }: { link: IntakeLink }) {
  const { t } = useTranslation()
  const label = t(`intake.link.${link.kind}`, { id: link.id, defaultValue: `${link.kind} #{{id}}` })
  const path = linkPath(link)
  return path ? <Link to={path} className="underline">{label}</Link> : <span>{label}</span>
}

function formatDuration(seconds: number | null, t: Translate) {
  if (seconds === null) return t('intake.record.none', { defaultValue: '—' })
  const hours = seconds / 3600
  return hours < 48
    ? t('intake.record.hours', { count: Math.round(hours * 10) / 10, defaultValue: '{{count}} h' })
    : t('intake.record.days', { count: Math.round(hours / 2.4) / 10, defaultValue: '{{count}} days' })
}

function DecisionForm({ item, companyId }: { item: IntakeItem; companyId: number }) {
  const { t } = useTranslation()
  const decide = useDecideIntake(companyId)
  const sprints = useSprints(companyId)
  const specifications = useSpecifications(companyId)
  // Nothing is filled in from the Support agent's suggestion: the decision
  // is the person's, and the two are compared afterwards.
  const [choice, setChoice] = useState<Choice>('accept_task')
  const [category, setCategory] = useState('')
  const [reason, setReason] = useState('')
  const [sprintId, setSprintId] = useState('')
  const [assignee, setAssignee] = useState('PO')
  const [specificationId, setSpecificationId] = useState('')
  const [duplicateOf, setDuplicateOf] = useState('')
  const [until, setUntil] = useState('')
  const openSprints = (sprints.data ?? []).filter(s => OPEN_SPRINTS.includes(s.status))
  const liveSpecs = (specifications.data?.specifications ?? []).filter(s => s.status !== 'superseded')
  const [dupKind, dupId] = duplicateOf.split(':')

  function decision(): IntakeDecision | null {
    const common = { category: category || null, reason: reason.trim() || null }
    if (choice === 'accept_task') return sprintId ? { action: 'accept', target: 'task', sprint_id: Number(sprintId), assignee, ...common } : null
    if (choice === 'accept_specification') return specificationId ? { action: 'accept', target: 'specification', specification_id: Number(specificationId), ...common } : null
    if (choice === 'decline') return reason.trim() ? { action: 'decline', ...common, reason: reason.trim() } : null
    if (choice === 'duplicate') {
      const id = Number(dupId)
      if (!id || (dupKind !== 'intake' && dupKind !== 'task')) return null
      return { action: 'duplicate', ...(dupKind === 'intake' ? { of_item_id: id } : { of_task_id: id }), ...common }
    }
    return until ? { action: 'snooze', until: new Date(until).toISOString(), ...common } : null
  }
  const ready = decision()
  const id = `intake-${item.id}`

  return (
    <form
      className="mt-2 grid gap-2 rounded-lg border border-surface-700 p-3 sm:grid-cols-2"
      onSubmit={event => {
        event.preventDefault()
        if (ready) decide.mutate({ id: item.id, decision: ready })
      }}
    >
      <label className="text-xs text-gray-400" htmlFor={`${id}-choice`}>
        {t('intake.decision', { defaultValue: 'Decision' })}
        <select id={`${id}-choice`} className="input mt-1 w-full" value={choice} onChange={e => setChoice(e.target.value as Choice)}>
          {CHOICES.map(c => <option key={c} value={c}>{t(`intake.choice.${c}`, { defaultValue: c })}</option>)}
        </select>
      </label>
      <label className="text-xs text-gray-400" htmlFor={`${id}-category`}>
        {t('intake.category', { defaultValue: 'Category' })}
        <select id={`${id}-category`} className="input mt-1 w-full" value={category} onChange={e => setCategory(e.target.value)}>
          <option value="">{t('intake.noCategory', { defaultValue: 'None' })}</option>
          {INTAKE_CATEGORIES.map(c => <option key={c} value={c}>{t(`intake.categories.${c}`, { defaultValue: c })}</option>)}
        </select>
      </label>
      {choice === 'accept_task' && (
        <>
          <label className="text-xs text-gray-400" htmlFor={`${id}-sprint`}>
            {t('intake.sprint', { defaultValue: 'Sprint' })}
            <select id={`${id}-sprint`} className="input mt-1 w-full" value={sprintId} onChange={e => setSprintId(e.target.value)}>
              <option value="">{t('intake.pickSprint', { defaultValue: 'Pick an open sprint' })}</option>
              {openSprints.map(s => <option key={s.id} value={s.id}>{s.sprint_name}</option>)}
            </select>
          </label>
          <label className="text-xs text-gray-400" htmlFor={`${id}-assignee`}>
            {t('intake.assignee', { defaultValue: 'Assignee (role)' })}
            <input id={`${id}-assignee`} className="input mt-1 w-full" maxLength={60} value={assignee} onChange={e => setAssignee(e.target.value)} />
          </label>
        </>
      )}
      {choice === 'accept_specification' && (
        <label className="text-xs text-gray-400 sm:col-span-2" htmlFor={`${id}-spec`}>
          {t('intake.specification', { defaultValue: 'Specification (an approved one gets a new revision)' })}
          <select id={`${id}-spec`} className="input mt-1 w-full" value={specificationId} onChange={e => setSpecificationId(e.target.value)}>
            <option value="">{t('intake.pickSpecification', { defaultValue: 'Pick a specification' })}</option>
            {liveSpecs.map(s => <option key={s.id} value={s.id}>{`${s.title} (v${s.version}, ${s.status})`}</option>)}
          </select>
        </label>
      )}
      {choice === 'duplicate' && (
        <label className="text-xs text-gray-400 sm:col-span-2" htmlFor={`${id}-dup`}>
          {t('intake.duplicateOf', { defaultValue: 'Duplicate of (intake:ID or task:ID)' })}
          <input id={`${id}-dup`} className="input mt-1 w-full" value={duplicateOf} onChange={e => setDuplicateOf(e.target.value.trim())} placeholder={t('intake.duplicatePlaceholder', { defaultValue: 'intake:12' })} />
        </label>
      )}
      {choice === 'snooze' && (
        <label className="text-xs text-gray-400 sm:col-span-2" htmlFor={`${id}-until`}>
          {t('intake.until', { defaultValue: 'Snooze until' })}
          <input id={`${id}-until`} type="date" className="input mt-1 w-full" value={until} onChange={e => setUntil(e.target.value)} />
        </label>
      )}
      <label className="text-xs text-gray-400 sm:col-span-2" htmlFor={`${id}-reason`}>
        {choice === 'decline'
          ? t('intake.reasonRequired', { defaultValue: 'Why it is declined (required)' })
          : t('intake.reason', { defaultValue: 'Reason (optional)' })}
        <input id={`${id}-reason`} className="input mt-1 w-full" maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} />
      </label>
      <div className="sm:col-span-2 flex items-center gap-3">
        <button type="submit" className="btn btn-primary btn-sm" disabled={!ready || decide.isPending}>
          {t('intake.submit', { defaultValue: 'Record the decision' })}
        </button>
        {decide.isError && (
          <span role="alert" className="text-xs text-red-400">
            {t('intake.decideError', { message: errorDetail(decide.error), defaultValue: 'Could not record it: {{message}}' })}
          </span>
        )}
      </div>
    </form>
  )
}

function Item({ item, companyId, canManage }: { item: IntakeItem; companyId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const decide = useDecideIntake(companyId)
  const decidable = item.status === 'pending' || item.status === 'snoozed'
  const reopenable = item.status !== 'pending' && item.status !== 'accepted'
  return (
    <li className="card p-3 text-sm" aria-label={item.title}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="font-medium text-gray-100">#{item.id} {item.title}</span>
        <span className="text-xs text-gray-400">
          {t(`intake.sources.${item.source}`, { defaultValue: item.source })}
          {item.submitted_by && ` · ${item.submitted_by}`}
          {` · ${t(`intake.statuses.${item.status}`, { defaultValue: item.status })}`}
        </span>
      </div>
      {item.body && <p className="mt-1 whitespace-pre-wrap text-xs text-gray-400">{item.body}</p>}
      {item.suggestion && (
        <p className="mt-1 text-xs text-amber-300">
          {item.suggestion.status
            ? t('intake.suggestion', {
                status: t(`intake.statuses.${item.suggestion.status}`, { defaultValue: item.suggestion.status }),
                category: item.suggestion.category ?? '—',
                reason: item.suggestion.reason ?? '',
                defaultValue: 'Support agent suggests (a claim): {{status}}, {{category}}. {{reason}}',
              })
            : t('intake.suggestionUnusable', { defaultValue: 'The Support agent gave no usable suggestion.' })}
          {item.suggestion.duplicate_of && ` (${item.suggestion.duplicate_of})`}
        </p>
      )}
      {item.decision && (
        <p className="mt-1 text-xs text-emerald-300">
          {t('intake.decided', {
            status: t(`intake.statuses.${item.decision.status}`, { defaultValue: item.decision.status ?? '' }),
            who: item.decision.by,
            defaultValue: 'Decided {{status}} by {{who}}',
          })}
          {item.decision.reason && `: ${item.decision.reason}`}
        </p>
      )}
      {item.outcome && <p className="mt-1 text-xs text-gray-300">{t('intake.became', { defaultValue: 'Became' })} <LinkTo link={item.outcome} /></p>}
      {item.duplicate_of && <p className="mt-1 text-xs text-gray-300">{t('intake.duplicates', { defaultValue: 'Duplicates' })} <LinkTo link={item.duplicate_of} /></p>}
      {item.snoozed_until && (
        <p className="mt-1 text-xs text-gray-400">
          {t('intake.snoozedUntil', { date: new Date(item.snoozed_until).toLocaleDateString(), defaultValue: 'Comes back on {{date}}' })}
        </p>
      )}
      {canManage && decidable && <DecisionForm item={item} companyId={companyId} />}
      {canManage && reopenable && (
        <button type="button" className="btn-ghost btn-sm mt-2" disabled={decide.isPending} onClick={() => decide.mutate({ id: item.id, decision: { action: 'reopen' } })}>
          {t('intake.reopen', { defaultValue: 'Back to pending' })}
        </button>
      )}
    </li>
  )
}

function SendForm({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const send = useSendIntake(companyId)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  return (
    <form
      className="flex flex-col gap-2 sm:flex-row"
      onSubmit={event => {
        event.preventDefault()
        if (!title.trim()) return
        send.mutate({ title: title.trim(), body: body.trim() }, { onSuccess: () => { setTitle(''); setBody('') } })
      }}
    >
      <label className="sr-only" htmlFor="intake-new-title">{t('intake.newTitle', { defaultValue: 'Request title' })}</label>
      <input id="intake-new-title" className="input flex-1" maxLength={200} value={title} onChange={e => setTitle(e.target.value)} placeholder={t('intake.newTitle', { defaultValue: 'Request title' })} />
      <label className="sr-only" htmlFor="intake-new-body">{t('intake.newBody', { defaultValue: 'Details' })}</label>
      <input id="intake-new-body" className="input flex-1" maxLength={8000} value={body} onChange={e => setBody(e.target.value)} placeholder={t('intake.newBody', { defaultValue: 'Details' })} />
      <button type="submit" className="btn btn-sm" disabled={!title.trim() || send.isPending}>
        <Send size={14} aria-hidden="true" />{t('intake.send', { defaultValue: 'Send to the queue' })}
      </button>
      {send.isError && <span role="alert" className="text-xs text-red-400">{errorDetail(send.error)}</span>}
    </form>
  )
}

function RecordCard({ companyId }: { companyId: number }) {
  const { t } = useTranslation()
  const record = useIntakeRecord(companyId)
  const data = record.data
  if (!data || data.received === 0) return null
  const share = data.recorded_share === null ? '—' : `${Math.round(data.recorded_share * 100)}%`
  return (
    <div className="grid gap-1 text-xs text-gray-400" aria-label={t('intake.record.title', { defaultValue: 'How the queue is doing' })}>
      <p>{t('intake.record.recorded', { share, recorded: data.recorded, received: data.received, defaultValue: '{{share}} closed with what it led to recorded ({{recorded}} of {{received}})' })}</p>
      <p>{t('intake.record.median', { duration: formatDuration(data.median_seconds_to_decision, t), defaultValue: 'Median time to a decision: {{duration}}' })}</p>
      {Object.entries(data.agreement).map(([category, tally]) => (
        <p key={category}>
          {t('intake.record.agreement', {
            category: t(`intake.categories.${category}`, { defaultValue: category }),
            agreed: tally.agreed,
            decided: tally.decided,
            defaultValue: '{{category}}: the Support agent matched the person on {{agreed}} of {{decided}}',
          })}
        </p>
      ))}
    </div>
  )
}

/**
 * The Inbox's intake queue (issue #532): incoming requests by status, the
 * Support agent's suggestion beside each (a claim), and for a Manager the
 * decision: accept as a task or into a specification revision, decline with
 * a reason, mark a duplicate, snooze until a date. Every decision is audited
 * and the item keeps the link to what it became.
 */
export function IntakeQueue({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const [status, setStatus] = useState<IntakeStatus>('pending')
  const intake = useIntake(companyId, status)
  const items = intake.data?.items ?? []
  return (
    <section aria-labelledby="inbox-intake" className="space-y-3">
      <h2 id="inbox-intake" className="flex items-center gap-2 text-sm font-semibold text-white">
        <InboxIcon size={16} aria-hidden="true" />
        {t('intake.title', { defaultValue: 'Incoming requests' })}
        <span className="badge">{intake.data?.pending ?? 0}</span>
      </h2>
      <p className="text-xs text-gray-500">
        {t('intake.hint', { defaultValue: 'Tickets, briefs, integrations and chat requests wait here until a Manager decides them. Snoozed ones come back on their date.' })}
      </p>
      <div role="group" aria-label={t('intake.filter', { defaultValue: 'Show requests by status' })} className="flex flex-wrap gap-1">
        {INTAKE_STATUSES.map(s => (
          <button key={s} type="button" aria-pressed={status === s} className={status === s ? 'btn btn-sm' : 'btn-ghost btn-sm'} onClick={() => setStatus(s)}>
            {t(`intake.statuses.${s}`, { defaultValue: s })}
          </button>
        ))}
      </div>
      <SendForm companyId={companyId} />
      {intake.isError && (
        <p role="alert" className="text-xs text-red-400">
          {t('intake.loadError', { message: errorDetail(intake.error), defaultValue: 'Could not load the requests: {{message}}' })}
        </p>
      )}
      {intake.isLoading ? (
        <div role="status" aria-label={t('common.loading')} className="skeleton h-16 rounded-lg" />
      ) : items.length === 0 ? (
        <p className="text-sm text-gray-500">{t('intake.empty', { defaultValue: 'No request with this status.' })}</p>
      ) : (
        <ul className="space-y-2">
          {items.map(item => <Item key={item.id} item={item} companyId={companyId} canManage={canManage} />)}
        </ul>
      )}
      <RecordCard companyId={companyId} />
    </section>
  )
}
