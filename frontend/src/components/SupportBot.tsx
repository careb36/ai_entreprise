import React, { useState, useRef, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError, type SupportTicket } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'
import { Bot, Bug, Send, X, Loader2, Ticket, ChevronRight, RefreshCw, CheckCircle2, Clock, Circle, UserRound } from 'lucide-react'
import { TICKET_STATUS_LOOK, TicketStatusBadge } from './ui'

// ── Types ─────────────────────────────────────────────────────────────────────

type Message  = { id: number; role: 'bot' | 'user'; text: string }
type View     = 'chat' | 'bug-form' | 'my-tickets' | 'ticket-detail'

type TicketSummary = SupportTicket

const SEVERITIES = ['critical', 'high', 'medium', 'low'] as const

// A field's label in the bug form: small, never below gray-400 on the dark panel.
const LABEL = 'mb-1 block text-[11px] font-medium text-gray-400'

// ── Component ─────────────────────────────────────────────────────────────────

export const SupportBot: React.FC = () => {
  const { t } = useTranslation()
  const [isOpen, setIsOpen]     = useState(false)
  const [view, setView]         = useState<View>('chat')

  // Draggable position (bottom-right default)
  const [pos, setPos] = useState({ x: 24, y: 24 })
  const dragging = useRef(false)
  const dragMoved = useRef(false)
  const dragOffset = useRef({ x: 0, y: 0 })
  const fabRef = useRef<HTMLDivElement>(null)

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest('[data-chat-panel]')) return
    dragging.current = true
    dragMoved.current = false
    dragOffset.current = { x: e.clientX + pos.x, y: e.clientY + pos.y }
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
  }, [pos])

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragging.current) return
    dragMoved.current = true
    const newX = dragOffset.current.x - e.clientX
    const newY = dragOffset.current.y - e.clientY
    const maxX = window.innerWidth - 56
    const maxY = window.innerHeight - 56
    setPos({
      x: Math.max(0, Math.min(newX, maxX)),
      y: Math.max(0, Math.min(newY, maxY)),
    })
  }, [])

  const onPointerUp = useCallback(() => { dragging.current = false }, [])

  // Chat
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput]       = useState('')
  const [isSending, setIsSending] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textareaRef    = useRef<HTMLTextAreaElement>(null)

  // Bug form
  const [title, setTitle]             = useState('')
  const [description, setDescription] = useState('')
  // Empty until the reporter picks one: a preset would pass for their choice.
  const [severity, setSeverity]       = useState<'' | 'low'|'medium'|'high'|'critical'>('')
  const [reporterEmail, setReporterEmail] = useState(
    () => localStorage.getItem('fabrique-support-email') || ''
  )
  const [submitting, setSubmitting]   = useState(false)

  // Tickets. A signed-in user sees the tickets reported with their account
  // email (support admins see any); the API answers 403 for another email.
  const accountEmail = useAuthStore(state => state.user?.email)
  const [email, setEmail] = useState(
    () => accountEmail || localStorage.getItem('fabrique-support-email') || ''
  )
  const [ticketsForbidden, setTicketsForbidden] = useState(false)
  // The profile can load after the bot mounts (login, first page load).
  useEffect(() => {
    if (accountEmail) setEmail(current => current || accountEmail)
  }, [accountEmail])
  const [tickets, setTickets]         = useState<TicketSummary[]>([])
  const [loadingTickets, setLoadingTickets] = useState(false)
  const [selectedTicket, setSelectedTicket] = useState<any>(null)

  // Reset greeting on language change
  useEffect(() => {
    setMessages([{ id: 0, role: 'bot', text: t('support.greeting') }])
  }, [t])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isSending])

  // ── Chat ──────────────────────────────────────────────────────────────────

  const addMsg = (role: 'bot' | 'user', text: string) =>
    setMessages(prev => [...prev, { id: Date.now() + Math.random(), role, text }])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || isSending) return
    setInput('')
    addMsg('user', text)
    setIsSending(true)
    try {
      // The backend reads the whole conversation, oldest first, as `messages`.
      const res = await api.supportChat([
        ...messages.slice(-10).map(m => ({
          role: m.role === 'bot' ? 'assistant' as const : 'user' as const,
          content: m.text,
        })),
        { role: 'user', content: text },
      ])
      addMsg('bot', res?.reply || t('support.noResponse'))
    } catch {
      addMsg('bot', t('support.chatUnavailable'))
    } finally {
      setIsSending(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  // Auto-grow textarea
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }

  // Relative date formatter
  const { i18n } = useTranslation()
  function relativeDate(iso: string | null): string {
    if (!iso) return ''
    try {
      const diff = Date.now() - new Date(iso).getTime()
      const mins = Math.floor(diff / 60000)
      if (mins < 1)  return t('glassroom.justNow', 'just now')
      if (mins < 60) return t('glassroom.minutesAgo', '{{n}} min ago').replace('{{n}}', String(mins))
      const hrs = Math.floor(mins / 60)
      if (hrs < 24)  return t('glassroom.hoursAgo', '{{n}} h ago').replace('{{n}}', String(hrs))
      return new Intl.DateTimeFormat(i18n.language, { month: 'short', day: 'numeric' }).format(new Date(iso))
    } catch { return '' }
  }

  // ── Bug form submit ────────────────────────────────────────────────────────

  const handleSubmitBug = async () => {
    if (!title.trim() || !description.trim()) return
    setSubmitting(true)
    try {
      const res = await api.reportBug({
        title: title.trim(),
        description: description.trim(),
        severity: severity || undefined,
        reporter_email: reporterEmail.trim() || undefined,
        browser_info: {
          userAgent: navigator.userAgent,
          url: window.location.href,
          viewport: `${window.innerWidth}x${window.innerHeight}`,
        },
      })
      // Save email for ticket lookup
      if (reporterEmail.trim()) {
        localStorage.setItem('fabrique-support-email', reporterEmail.trim())
        setEmail(reporterEmail.trim())
      }
      // Reset form
      setTitle(''); setDescription(''); setSeverity(''); setReporterEmail('')
      // Return to chat with confirmation
      setView('chat')
      addMsg('bot',
        `✅ ${t('support.ticketCreated', { num: res.ticket_num })}\n\n` +
        `📋 ${t('support.ticketTrackHint')}`
      )
    } catch {
      addMsg('bot', t('common.error'))
      setView('chat')
    } finally {
      setSubmitting(false)
    }
  }

  // ── Ticket list ────────────────────────────────────────────────────────────

  const loadTickets = async (emailOverride?: string) => {
    const q = (emailOverride || email).trim()
    if (!q) return
    setLoadingTickets(true)
    setTicketsForbidden(false)
    try {
      const res = await api.listTickets({ reporter_email: q })
      setTickets(res.tickets || [])
    } catch (error) {
      setTicketsForbidden(error instanceof ApiError && error.status === 403)
      setTickets([])
    } finally {
      setLoadingTickets(false)
    }
  }

  const loadTicketDetail = async (num: string) => {
    try {
      const res = await api.getTicket(num)
      setSelectedTicket(res)
      setView('ticket-detail')
    } catch { /* ignore */ }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  // An unknown status reads as open, as it always has here.
  const StatusBadge = ({ status }: { status: string }) => (
    <TicketStatusBadge status={status in TICKET_STATUS_LOOK ? status : 'open'} />
  )

  const headerTitle = () => {
    if (view === 'bug-form')      return t('support.reportBug')
    if (view === 'my-tickets')    return t('support.myTickets')
    if (view === 'ticket-detail') return selectedTicket?.ticket_num ?? '...'
    return t('support.title')
  }

  const HeaderIcon = view === 'bug-form' ? Bug : view === 'chat' ? Bot : Ticket

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div
      ref={fabRef}
      className="fixed z-50"
      style={{ right: pos.x, bottom: pos.y }}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
    >
      <button
        onPointerDown={onPointerDown}
        onClick={() => { if (!dragMoved.current) setIsOpen(p => !p) }}
        className="focus-ring flex h-12 w-12 cursor-grab touch-none select-none items-center justify-center rounded-full border border-brand-500/40 bg-brand-600 text-white shadow-2xl transition-transform hover:scale-105 hover:bg-brand-700 active:cursor-grabbing motion-reduce:transition-none motion-reduce:hover:scale-100"
        title={t('support.open')}>
        {isOpen ? <X size={22} aria-hidden="true" /> : <Bot size={24} aria-hidden="true" />}
      </button>

      {isOpen && (
        <div data-chat-panel className="absolute bottom-16 right-0 flex max-h-[80vh] w-[min(340px,calc(100vw-2rem))] flex-col overflow-hidden rounded-xl border border-surface-700 bg-surface-900 shadow-2xl">

          {/* Header */}
          <div className="flex items-center justify-between gap-2 border-b border-surface-700 px-3 py-2">
            <div className="flex min-w-0 items-center gap-1.5">
              {view !== 'chat' && (
                <button type="button" aria-label={t('common.back')} onClick={() => setView('chat')} className="btn-tool w-8 shrink-0 justify-center px-0">
                  <ChevronRight size={16} className="rotate-180" aria-hidden="true" />
                </button>
              )}
              <HeaderIcon size={15} aria-hidden="true" className="shrink-0 text-brand-300" />
              <div className="min-w-0">
                <h3 className="truncate text-[13px] font-semibold text-gray-100">{headerTitle()}</h3>
                <p className="text-meta">{t('support.online')}</p>
              </div>
            </div>
            <div className="flex shrink-0 gap-0.5">
              <button type="button" onClick={() => setView('bug-form')}
                aria-pressed={view === 'bug-form'}
                className={`btn-tool w-8 justify-center px-0 ${view === 'bug-form' ? 'btn-tool-on' : ''}`}
                title={t('support.reportBug')}><Bug size={15} aria-hidden="true" /></button>
              <button type="button" onClick={() => { setView('my-tickets'); if (email) loadTickets() }}
                aria-pressed={view === 'my-tickets' || view === 'ticket-detail'}
                className={`btn-tool w-8 justify-center px-0 ${view === 'my-tickets' || view === 'ticket-detail' ? 'btn-tool-on' : ''}`}
                title={t('support.myTickets')}><Ticket size={15} aria-hidden="true" /></button>
            </div>
          </div>

          {/* ── CHAT ──────────────────────────────────────────────────── */}
          {view === 'chat' && (<>
            <div className="flex h-56 flex-col gap-2 overflow-y-auto bg-surface-950/60 p-3">
              {messages.map(m => (
                <div key={m.id}
                  className={`max-w-[90%] whitespace-pre-wrap break-words rounded-xl px-3 py-2 text-[13px] leading-snug ${
                    m.role === 'bot'
                      ? 'self-start rounded-tl-none border border-surface-700 bg-surface-800 text-gray-100'
                      : 'self-end rounded-tr-none bg-brand-600 text-white'
                  }`}>{m.text}</div>
              ))}
              {isSending && (
                <div role="status" className="flex items-center gap-2 self-start rounded-xl rounded-tl-none border border-surface-700 bg-surface-800 px-3 py-2 text-gray-300">
                  <Loader2 size={13} aria-hidden="true" className="animate-spin motion-reduce:animate-none" /><span className="text-xs">{t('support.thinking')}</span>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
            <div className="border-t border-surface-700 p-3">
              <div className="flex items-end gap-2">
                <textarea ref={textareaRef} value={input} aria-label={t('support.inputLabel')}
                  onChange={handleInputChange} onKeyDown={handleKeyDown}
                  placeholder={t('support.inputPlaceholder')} rows={2}
                  className="input flex-1 resize-none text-[13px]" />
                <button type="button" aria-label={t('support.send')} onClick={handleSend} disabled={isSending || !input.trim()}
                  className="btn-primary self-end px-2.5">
                  <Send size={16} aria-hidden="true" />
                </button>
              </div>
              <p className="text-meta mt-1">{t('support.enterToSend')}</p>
            </div>
          </>)}

          {/* ── BUG FORM — simple, fast ─────────────────────────────── */}
          {view === 'bug-form' && (
            <div className="flex flex-col overflow-y-auto">
              <div className="space-y-3 p-3">
                <p className="text-xs text-gray-400">{t('support.form.hint')}</p>

                <label className="block">
                  <span className={LABEL}>{t('support.form.title')} *</span>
                  <input value={title} onChange={e => setTitle(e.target.value)}
                    placeholder={t('support.form.titlePlaceholder')}
                    className="input text-[13px]" />
                </label>

                <label className="block">
                  <span className={LABEL}>{t('support.description')} *</span>
                  <textarea value={description} onChange={e => setDescription(e.target.value)}
                    placeholder={t('support.form.descPlaceholder')} rows={3}
                    className="input resize-none text-[13px]" />
                </label>

                <label className="block">
                  <span className={LABEL}>{t('support.severity')}</span>
                  <select value={severity} onChange={e => setSeverity(e.target.value as any)}
                    className="select text-[13px]">
                    <option value="">{t('support.severityNotSure', { defaultValue: 'Not sure' })}</option>
                    {SEVERITIES.map(s => (
                      <option key={s} value={s}>{t(`support.${s}`)}</option>
                    ))}
                  </select>
                </label>

                <label className="block">
                  <span className={LABEL}>{t('support.emailToTrack')}</span>
                  <input type="email" value={reporterEmail} onChange={e => setReporterEmail(e.target.value)}
                    placeholder={t('support.emailPlaceholder', { defaultValue: 'you@company.com' })}
                    className="input text-[13px]" />
                  <span className="text-meta mt-1 block">{t('support.emailHint')}</span>
                </label>
              </div>

              <div className="flex gap-2 border-t border-surface-700 p-3">
                <button type="button" onClick={() => setView('chat')}
                  className="btn-secondary btn-sm flex-1">{t('common.cancel')}</button>
                <button type="button" onClick={handleSubmitBug}
                  disabled={submitting || !title.trim() || !description.trim()}
                  className="btn-primary btn-sm flex-1">
                  {submitting && <Loader2 size={14} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />}
                  {submitting ? t('support.submitting') : t('support.submit')}
                </button>
              </div>
            </div>
          )}

          {/* ── MY TICKETS — status tracking ───────────────────────── */}
          {view === 'my-tickets' && (
            <div className="flex h-[340px] flex-col">
              <div className="flex gap-2 border-b border-surface-700 p-3">
                <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                  aria-label={t('support.emailToSearch')}
                  onKeyDown={e => e.key==='Enter' && loadTickets()}
                  placeholder={t('support.emailToSearch')}
                  className="input control-sm flex-1" />
                <button type="button" aria-label={t('support.searchTickets')} onClick={() => loadTickets()} disabled={!email.trim() || loadingTickets}
                  className="btn-tool w-8 justify-center border border-surface-700 px-0">
                  {loadingTickets
                    ? <Loader2 size={15} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
                    : <RefreshCw size={15} aria-hidden="true" />}
                </button>
              </div>

              <div className="data-list flex-1 overflow-y-auto">
                {!loadingTickets && tickets.length === 0 && (
                  <p className="mt-10 px-4 text-center text-xs text-gray-400">
                    {ticketsForbidden
                      ? t('support.ownTicketsOnly', { email: accountEmail ?? '', defaultValue: 'You can only see the tickets reported with your account email ({{email}}).' })
                      : t('support.noTickets')}
                  </p>
                )}
                {tickets.map(tk => (
                  <button type="button" key={tk.ticket_num} onClick={() => loadTicketDetail(tk.ticket_num)}
                    className="data-row-link w-full text-left">
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <span className="font-mono text-[11px] font-semibold text-brand-300">{tk.ticket_num}</span>
                      <StatusBadge status={tk.status} />
                    </div>
                    <p className="truncate text-[13px] text-gray-100">{tk.title}</p>
                    <p className="text-meta mt-0.5 tabular-nums">
                      {relativeDate(tk.updated_at)}
                    </p>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* ── TICKET DETAIL — what the user cares about ──────────── */}
          {view === 'ticket-detail' && selectedTicket && (
            <div className="max-h-[380px] overflow-y-auto">
              <div className="space-y-4 p-3">

                {/* Status — front and centre */}
                <div className="rounded-lg border border-surface-700 bg-surface-950/60 p-3 text-center">
                  <p className="text-meta mb-1 font-medium uppercase tracking-wide">{t('support.currentStatus')}</p>
                  <div className="flex justify-center"><StatusBadge status={selectedTicket.status} /></div>
                  {selectedTicket.status === 'resolved' && selectedTicket.resolution && (
                    <div className="mt-2 rounded-md border border-emerald-800/50 bg-emerald-950/40 p-2 text-left">
                      <p className="text-[11px] font-medium uppercase tracking-wide text-emerald-300">{t('support.resolution')}</p>
                      <p className="mt-0.5 text-xs text-emerald-100">{selectedTicket.resolution}</p>
                    </div>
                  )}
                </div>

                {/* Ticket info */}
                <div>
                  <p className="text-[13px] font-semibold text-gray-100">{selectedTicket.title}</p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2">
                    {selectedTicket.severity && <span className="chip">{selectedTicket.severity}</span>}
                    {selectedTicket.category && <span className="chip">{selectedTicket.category}</span>}
                    {selectedTicket.assigned_to && (
                      <span className="text-meta inline-flex items-center gap-1">
                        <UserRound size={11} aria-hidden="true" />{selectedTicket.assigned_to}
                      </span>
                    )}
                  </div>
                </div>

                <p className="text-xs leading-relaxed text-gray-300">{selectedTicket.description}</p>

                {selectedTicket.agent_reply && (
                  // The agent's reply is a claim: dashed, as the board's reported signals are.
                  <div className="rounded-lg border border-dashed border-surface-700 p-3">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-gray-400">{t('support.agentReply', { defaultValue: 'Reply from the Support agent' })}</p>
                    <p className="mt-0.5 whitespace-pre-wrap text-xs text-gray-200">{selectedTicket.agent_reply}</p>
                  </div>
                )}

                {/* Timeline */}
                <div className="space-y-2 border-t border-surface-700 pt-3">
                  <p className="text-[11px] font-medium uppercase tracking-wide text-gray-400">{t('support.timeline')}</p>
                  <div className="text-meta space-y-1.5">
                    <div className="flex items-start gap-2">
                      <Circle size={10} aria-hidden="true" className="mt-0.5 shrink-0 text-brand-300" />
                      <span>{t('support.created')}: <span className="text-gray-200">{relativeDate(selectedTicket.created_at)}</span></span>
                    </div>
                    {selectedTicket.updated_at !== selectedTicket.created_at && (
                      <div className="flex items-start gap-2">
                        <Clock size={10} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-300" />
                        <span>{t('support.updated')}: <span className="text-gray-200">{relativeDate(selectedTicket.updated_at)}</span></span>
                      </div>
                    )}
                    {selectedTicket.resolved_at && (
                      <div className="flex items-start gap-2">
                        <CheckCircle2 size={10} aria-hidden="true" className="mt-0.5 shrink-0 text-emerald-300" />
                        <span>{t('support.resolved')}: <span className="text-gray-200">{new Date(selectedTicket.resolved_at).toLocaleString()}</span></span>
                      </div>
                    )}
                  </div>
                </div>

              </div>
            </div>
          )}

        </div>
      )}
    </div>
  )
}
