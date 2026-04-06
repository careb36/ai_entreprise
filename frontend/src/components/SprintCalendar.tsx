import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Calendar, SlotInfo, Views, momentLocalizer } from 'react-big-calendar'
import { CalendarDays, Expand, Minimize, X } from 'lucide-react'
import moment from 'moment'

interface SprintCalendarProps {
  sprintId: string
  sprintName?: string
  startDate?: string
  durationDays?: number
}

interface SprintEvent {
  title: string
  start: Date
  end: Date
  allDay?: boolean
  kind: 'window' | 'checkpoint' | 'deadline'
  description?: string
}

const localizer = momentLocalizer(moment)

function toDate(value?: string): Date {
  if (!value) return new Date()
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? new Date() : parsed
}

export default function SprintCalendar({
  sprintId,
  sprintName,
  startDate,
  durationDays,
}: SprintCalendarProps) {
  const { t, i18n } = useTranslation()
  const [view, setView] = useState<(typeof Views)[keyof typeof Views]>(Views.MONTH)
  const [currentDate, setCurrentDate] = useState(new Date())
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [selectedEvent, setSelectedEvent] = useState<SprintEvent | null>(null)
  const start = toDate(startDate)
  const totalDays = Math.max(1, durationDays || 14)

  const end = useMemo(() => {
    const next = new Date(start)
    next.setDate(start.getDate() + totalDays - 1)
    return next
  }, [start, totalDays])

  const baseEvents = useMemo<SprintEvent[]>(() => {
    const midpoint = new Date(start)
    midpoint.setDate(start.getDate() + Math.max(1, Math.floor(totalDays / 2)))

    const prep = new Date(start)
    prep.setDate(start.getDate() + 1)

    return [
      {
        title: `${sprintName || 'Sprint'} window`,
        start,
        end,
        allDay: true,
        kind: 'window',
        description: 'Main sprint delivery window',
      },
      {
        title: 'Sprint planning',
        start,
        end: prep,
        kind: 'checkpoint',
        description: 'Planning and backlog alignment',
      },
      {
        title: 'Mid-sprint checkpoint',
        start: midpoint,
        end: midpoint,
        kind: 'checkpoint',
        description: 'Scope, blockers and quality checkpoint',
      },
      {
        title: 'Sprint deadline',
        start: end,
        end,
        kind: 'deadline',
        description: 'Final deadline and closure target',
      },
    ]
  }, [start, end, sprintName, totalDays])

  const [events, setEvents] = useState<SprintEvent[]>(baseEvents)

  useEffect(() => {
    setEvents(baseEvents)
  }, [baseEvents])

  const progress = useMemo(() => {
    const now = new Date()
    if (now <= start) return 0
    if (now >= end) return 100
    const total = end.getTime() - start.getTime()
    const elapsed = now.getTime() - start.getTime()
    return Math.min(100, Math.max(0, Math.round((elapsed / total) * 100)))
  }, [start, end])

  const eventStyleGetter = (event: SprintEvent) => {
    if (event.kind === 'deadline') {
      return { className: 'sprint-rbc-event sprint-rbc-deadline' }
    }
    if (event.kind === 'checkpoint') {
      return { className: 'sprint-rbc-event sprint-rbc-checkpoint' }
    }
    return { className: 'sprint-rbc-event sprint-rbc-window' }
  }

  const culture = i18n.language.startsWith('es') ? 'es' : 'en'

  useEffect(() => {
    moment.locale(culture)
  }, [culture])

  function handleSelectSlot(slot: SlotInfo) {
    const startDate = slot.start as Date
    const endDate = slot.end as Date
    const dynamicEvent: SprintEvent = {
      title: 'Focus block',
      start: startDate,
      end: endDate,
      kind: 'checkpoint',
      description: 'Dynamic focus block created from calendar selection',
    }
    setEvents(prev => [...prev, dynamicEvent])
    setSelectedEvent(dynamicEvent)
  }

  return (
    <div className={isFullscreen ? 'fixed inset-0 z-50 bg-[#f6f8fb] p-3 md:p-6' : 'h-full flex flex-col google-calendar-shell'}>
      <div className="px-4 py-3 border-b border-[#dde3ea] bg-white">
        <div className="flex items-center gap-2">
          <CalendarDays size={14} className="text-[#1a73e8]" />
          <h3 className="text-sm font-semibold text-[#1f1f1f]">Sprint Calendar</h3>
          <button
            type="button"
            className="ml-auto google-calendar-toggle"
            onClick={() => setIsFullscreen(prev => !prev)}
          >
            {isFullscreen ? <Minimize size={14} /> : <Expand size={14} />}
            {isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
          </button>
        </div>
        <p className="text-xs text-[#5f6368] mt-1">Sprint #{sprintId} · {sprintName || 'Current Sprint'}</p>
      </div>

      <div className="p-4 space-y-3 overflow-auto h-full sprint-rbc google-calendar-surface">
        <div>
          <div className="flex justify-between text-xs text-[#5f6368] mb-1">
            <span>{t('workspace.sprintBoard')}</span>
            <span>{progress}%</span>
          </div>
          <div className="h-2 rounded-full bg-[#e8eaed] overflow-hidden">
            <div className="h-full bg-[#1a73e8]" style={{ width: `${progress}%` }} />
          </div>
        </div>

        <div className={isFullscreen ? 'h-[calc(100vh-11rem)]' : 'h-[26rem] min-h-[20rem]'}>
          <Calendar
            localizer={localizer}
            culture={culture}
            events={events}
            startAccessor="start"
            endAccessor="end"
            views={[Views.DAY, Views.WEEK, Views.MONTH, Views.AGENDA]}
            view={view}
            date={currentDate}
            step={30}
            timeslots={2}
            showMultiDayTimes
            onView={(next) => setView(next)}
            onNavigate={(nextDate) => setCurrentDate(nextDate)}
            onSelectEvent={(event) => setSelectedEvent(event as SprintEvent)}
            onSelectSlot={handleSelectSlot}
            selectable
            eventPropGetter={eventStyleGetter}
            popup
            style={{ height: '100%' }}
          />
        </div>
      </div>

      {selectedEvent && (
        <div className="fixed inset-0 z-[60] bg-black/35 backdrop-blur-[1px] p-3 md:p-8">
          <div className="w-full h-full rounded-2xl border border-[#dde3ea] bg-white shadow-[0_8px_28px_rgba(32,33,36,0.18)] flex flex-col">
            <div className="px-5 py-4 border-b border-[#e6e9ef] bg-white flex items-center gap-3">
              <CalendarDays size={16} className="text-[#1a73e8]" />
              <h4 className="text-base font-semibold text-[#202124]">{selectedEvent.title}</h4>
              <span className="ml-auto text-xs text-[#5f6368]">{moment(selectedEvent.start).format('LLL')} - {moment(selectedEvent.end).format('LLL')}</span>
              <button type="button" className="google-calendar-toggle" onClick={() => setSelectedEvent(null)}>
                <X size={14} />
                Close
              </button>
            </div>
            <div className="p-6 overflow-auto">
              <div className="max-w-4xl space-y-4">
                <p className="text-sm text-[#3c4043]">{selectedEvent.description || 'No details provided.'}</p>
                <div className="rounded-xl border border-[#e6e9ef] bg-[#f8fafd] p-4">
                  <p className="text-xs text-[#5f6368] uppercase tracking-wider">Event Type</p>
                  <p className="mt-1 text-sm text-[#202124] capitalize">{selectedEvent.kind}</p>
                </div>
                <div className="rounded-xl border border-[#e6e9ef] bg-[#f8fafd] p-4">
                  <p className="text-xs text-[#5f6368] uppercase tracking-wider">Actions</p>
                  <div className="mt-2 flex gap-2">
                    <button type="button" className="google-calendar-primary" onClick={() => setIsFullscreen(true)}>Open full calendar</button>
                    <button type="button" className="google-calendar-toggle" onClick={() => setSelectedEvent(null)}>Back to board</button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
