import React from 'react'
import { useTranslation } from 'react-i18next'
import { usePreviewRunWindow, useRunWindow, useSetRunWindow } from '../hooks/useRunWindow'
import { errorDetail, type RunWindow, type RunWindowInterval, type RunWindowStatus } from '../services/api'
import { Panel } from './ui'

const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
const CUSTOM = 'custom'
const OFF = 'off'

function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

/** "2026-11-25 01:00 – 06:00" from two ISO 8601 times in the window's own zone. */
function spanLabel(from: string, to: string): string {
  const sameDay = from.slice(0, 10) === to.slice(0, 10)
  return `${from.slice(0, 10)} ${from.slice(11, 16)} – ${sameDay ? '' : `${to.slice(0, 10)} `}${to.slice(11, 16)}`
}

interface Draft {
  mode: string
  timezone: string
  intervals: RunWindowInterval[]
  calendar: string
  dates: string
}

function draftOf(window: RunWindow | null): Draft {
  if (!window) return { mode: OFF, timezone: browserZone(), intervals: [], calendar: '', dates: '' }
  return {
    mode: window.preset ?? CUSTOM,
    timezone: window.timezone,
    intervals: window.intervals,
    calendar: window.holidays.calendar ?? '',
    dates: window.holidays.dates.join(', '),
  }
}

function windowOf(draft: Draft): RunWindow | null {
  if (draft.mode === OFF) return null
  return {
    timezone: draft.timezone.trim(),
    preset: draft.mode === CUSTOM ? null : draft.mode,
    intervals: draft.mode === CUSTOM ? draft.intervals : [],
    holidays: {
      calendar: draft.calendar || null,
      dates: draft.dates.split(/[\s,]+/).filter(Boolean),
    },
  }
}

function Preview({ status }: { status: RunWindowStatus }) {
  const { t } = useTranslation()
  if (!status.run_window) return <p className="text-xs text-gray-400">{t('runWindow.offHint')}</p>
  return (
    <div className="space-y-1 text-xs text-gray-300">
      <p role="status">
        {status.open
          ? t('runWindow.openNow')
          : t('runWindow.closedNow', { time: status.next_open ? `${status.next_open.slice(0, 10)} ${status.next_open.slice(11, 16)}` : '—' })}
      </p>
      <p className="text-gray-400">{t('runWindow.previewTitle', { zone: status.run_window.timezone })}</p>
      <ul className="list-inside list-disc" aria-label={t('runWindow.previewTitle', { zone: status.run_window.timezone })}>
        {status.preview.map(span => <li key={span.from}>{spanLabel(span.from, span.to)}</li>)}
      </ul>
    </div>
  )
}

function IntervalRow({ interval, onChange, onRemove }: { interval: RunWindowInterval; onChange: (next: RunWindowInterval) => void; onRemove: () => void }) {
  const { t } = useTranslation()
  const toggle = (day: string) => onChange({
    ...interval,
    days: interval.days.includes(day) ? interval.days.filter(d => d !== day) : [...interval.days, day],
  })
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs text-gray-300">
      {DAYS.map(day => (
        <label key={day} className="flex items-center gap-1">
          <input type="checkbox" checked={interval.days.includes(day)} onChange={() => toggle(day)} />
          {t(`runWindow.days.${day}`)}
        </label>
      ))}
      <label className="flex items-center gap-1">{t('runWindow.from')}
        <input type="time" value={interval.start} onChange={e => onChange({ ...interval, start: e.target.value })}
          className="rounded border border-surface-700 bg-surface-900 px-1 text-gray-200" />
      </label>
      <label className="flex items-center gap-1">{t('runWindow.to')}
        <input type="time" value={interval.end === '24:00' ? '00:00' : interval.end} onChange={e => onChange({ ...interval, end: e.target.value })}
          className="rounded border border-surface-700 bg-surface-900 px-1 text-gray-200" />
      </label>
      <button type="button" className="btn-ghost btn-sm" onClick={onRemove}>{t('runWindow.removeInterval')}</button>
    </div>
  )
}

/**
 * When work that can wait may start (issue #736): off, a provider's off-peak
 * hours, or intervals of the company's own, in a named time zone (the
 * browser's by default), with an optional holiday calendar. Any member reads
 * it and previews the next 7 days; only an Owner saves it, each change audited.
 * Urgent sprints and a model call already running are never held.
 */
export default function RunWindowPanel({ companyId, isOwner }: { companyId: number | null; isOwner: boolean }) {
  const { t } = useTranslation()
  const setting = useRunWindow(companyId)
  const save = useSetRunWindow(companyId)
  const preview = usePreviewRunWindow(companyId)
  const [draft, setDraft] = React.useState<Draft | null>(null)

  if (setting.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(setting.error)}</div>
  if (!setting.data) return null
  const state = setting.data
  const current = draft ?? draftOf(state.run_window)
  const edit = (change: Partial<Draft>) => { preview.reset(); setDraft({ ...current, ...change }) }
  const preset = state.presets[current.mode]
  const shown: RunWindowStatus = preview.data ?? state

  return (
    <Panel title={t('runWindow.title')}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-400">{t('runWindow.hint')}</p>
        <fieldset className="space-y-2" disabled={!isOwner || save.isPending}>
          <legend className="sr-only">{t('runWindow.title')}</legend>
          {[OFF, ...Object.keys(state.presets), CUSTOM].map(mode => (
            <label key={mode} className="flex items-start gap-2 text-[13px] text-gray-200">
              <input type="radio" name="run-window-mode" className="mt-0.5 h-4 w-4 accent-brand-500" value={mode}
                checked={current.mode === mode}
                onChange={() => edit({ mode, intervals: mode === CUSTOM && !current.intervals.length ? [{ days: [...DAYS], start: '22:00', end: '06:00' }] : current.intervals })} />
              <span className="font-medium">{t(`runWindow.mode.${mode.replace(/[^a-z]/g, '_')}`, { defaultValue: mode })}</span>
            </label>
          ))}
          {preset && (
            <p className="text-xs text-gray-400">
              {t('runWindow.presetSource', { price: preset.off_peak_price, date: preset.read_on })}{' '}
              <a href={preset.source} target="_blank" rel="noreferrer" className="underline">{preset.source}</a>
            </p>
          )}
          {current.mode !== OFF && (
            <>
              <label className="flex items-center gap-2 text-xs text-gray-300">{t('runWindow.timezone')}
                <input value={current.timezone} onChange={e => edit({ timezone: e.target.value })}
                  className="rounded border border-surface-700 bg-surface-900 px-1.5 py-0.5 text-gray-200" />
              </label>
              {current.mode === CUSTOM && (
                <div className="space-y-2">
                  {current.intervals.map((interval, index) => (
                    <IntervalRow key={index} interval={interval}
                      onChange={next => edit({ intervals: current.intervals.map((old, i) => (i === index ? next : old)) })}
                      onRemove={() => edit({ intervals: current.intervals.filter((_, i) => i !== index) })} />
                  ))}
                  <button type="button" className="btn-ghost btn-sm"
                    onClick={() => edit({ intervals: [...current.intervals, { days: [...DAYS], start: '22:00', end: '06:00' }] })}>
                    {t('runWindow.addInterval')}
                  </button>
                </div>
              )}
              <label className="flex items-center gap-2 text-xs text-gray-300">{t('runWindow.calendar')}
                <select value={current.calendar} onChange={e => edit({ calendar: e.target.value })}
                  className="rounded border border-surface-700 bg-surface-900 px-1.5 py-0.5 text-gray-200">
                  <option value="">{t('runWindow.noCalendar')}</option>
                  {Object.entries(state.calendars).map(([key, calendar]) => (
                    <option key={key} value={key}>{calendar.name}</option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 text-xs text-gray-300">{t('runWindow.dates')}
                <input value={current.dates} placeholder="2026-12-31, 2027-01-01" onChange={e => edit({ dates: e.target.value })}
                  className="flex-1 rounded border border-surface-700 bg-surface-900 px-1.5 py-0.5 text-gray-200" />
              </label>
            </>
          )}
        </fieldset>
        {(save.isError || preview.isError) && (
          <div role="alert" className="alert-error text-sm">{errorDetail(save.error ?? preview.error)}</div>
        )}
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-ghost btn-sm" disabled={preview.isPending}
            onClick={() => preview.mutate(windowOf(current))}>{t('runWindow.preview')}</button>
          {isOwner && (
            <button type="button" className="btn-primary btn-sm" disabled={save.isPending || draft === null}
              onClick={() => save.mutate(windowOf(current), { onSuccess: () => { setDraft(null); preview.reset() } })}>
              {t('runWindow.save')}
            </button>
          )}
        </div>
        <Preview status={shown} />
        {!isOwner && <p className="text-xs text-gray-400">{t('runWindow.ownerOnly')}</p>}
      </div>
    </Panel>
  )
}
