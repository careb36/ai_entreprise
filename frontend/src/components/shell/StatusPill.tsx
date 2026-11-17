import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { api } from '../../services/api'
import type { Translate } from '../../i18n/translate'

// ── Status pill (live backend health) ────────────────────────────────────────
const HEALTH_COMPONENTS = ['postgres', 'nats', 'llm_proxy', 'neo4j'] as const

export function StatusPill({ t }: { t: Translate }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const { isError } = useQuery({
    queryKey: ['health'],
    queryFn: () => api.health(),
    refetchInterval: 15000,
    retry: 1,
  })
  // Per-service probes are slow when a service is down: only while open.
  const deep = useQuery({
    queryKey: ['health', 'deep'],
    queryFn: () => api.deepHealth(),
    enabled: open,
    staleTime: 0,
    retry: false,
  })
  useEffect(() => {
    if (!open) return
    const close = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [open])
  const online = !isError
  return (
    <div ref={ref} className="relative hidden xl:block">
      <button type="button" onClick={() => setOpen(value => !value)}
        onKeyDown={event => { if (event.key === 'Escape') setOpen(false) }}
        aria-expanded={open} aria-controls="service-status"
        className="btn-tool border border-surface-700 text-gray-300">
        <span aria-hidden="true" className={online ? 'status-dot-pulse motion-reduce:animate-none' : 'status-dot bg-red-500'} />
        {online ? t('nav.online', { defaultValue: 'Online' }) : t('nav.offline', { defaultValue: 'Offline' })}
      </button>
      {open && (
        <div id="service-status" role="dialog" aria-label={t('health.title', { defaultValue: 'Service status' })}
          className="absolute right-0 top-full z-20 mt-2 w-72 rounded-xl border border-surface-700 bg-surface-900 p-3 text-xs shadow-card-md">
          <p className="mb-2 font-semibold text-gray-200">{t('health.title', { defaultValue: 'Service status' })}</p>
          {deep.isLoading ? (
            <p role="status" className="flex items-center gap-2 text-gray-400">
              <Loader2 size={12} aria-hidden="true" className="animate-spin motion-reduce:animate-none" /> {t('health.checking', { defaultValue: 'Checking services…' })}
            </p>
          ) : deep.isError || !deep.data ? (
            <p role="alert" className="text-red-300">{t('health.error', { defaultValue: 'Could not check the services.' })}</p>
          ) : (
            <>
              <p className="mb-2 text-gray-400">{t(`health.overall.${deep.data.status}`, { defaultValue: deep.data.status })}</p>
              <ul className="space-y-1.5">
                {HEALTH_COMPONENTS.filter(name => deep.data.components[name]).map(name => {
                  const component = deep.data.components[name]
                  const up = component.status === 'up'
                  const off = component.status === 'disabled'
                  return (
                    <li key={name}>
                      <span className="flex items-center gap-2">
                        <span className={`h-2 w-2 shrink-0 rounded-full ${up ? 'bg-emerald-400' : off ? 'bg-gray-500' : 'bg-red-500'}`} aria-hidden="true" />
                        <span className="text-gray-200">{t(`health.components.${name}`)}</span>
                        <span className={`ml-auto ${up ? 'text-emerald-300' : off ? 'text-gray-400' : 'text-red-300'}`}>
                          {up ? t('health.up', { defaultValue: 'up' }) : off ? t('health.disabled', { defaultValue: 'off' }) : t('health.down', { defaultValue: 'down' })}
                        </span>
                      </span>
                      {!up && !off && component.reason && <span className="block break-words pl-4 text-gray-500">{component.reason}</span>}
                    </li>
                  )
                })}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  )
}
