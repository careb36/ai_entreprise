import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { errorDetail, type WorkspaceHost } from '../../services/api'
import { WorkspaceDaemonHelp } from '../../components/WorkspaceDaemonHelp'

const platformName = (t: TFunction, platform: string) =>
  t(`workspaceDaemon.platforms.${platform}`, { defaultValue: platform })

/** Whether the native workspace daemon answers, and on which machine. */
export function DaemonStatusLine({ host, connected, state, reason, isPending, error, fallbackPlatform }: {
  host: WorkspaceHost | null | undefined
  connected: boolean | undefined
  state?: 'connected' | 'absent' | 'unknown' | 'incompatible'
  reason?: string | null
  isPending: boolean
  error: unknown
  fallbackPlatform: string | undefined
}) {
  const { t } = useTranslation()
  if (isPending) return null
  if (connected && host) {
    return (
      <p role="status" className="flex items-center gap-2 text-xs text-green-300">
        <span className="status-dot bg-green-500" aria-hidden="true" />
        {t('workspaceDaemon.connected', { hostname: host.hostname, platform: platformName(t, host.platform) })}
      </p>
    )
  }
  // Incompatible is not absent either: it runs, but speaks another protocol version.
  if (state === 'incompatible') {
    return (
      <p role="status" className="flex items-center gap-2 text-xs text-amber-300">
        <span className="status-dot bg-amber-500" aria-hidden="true" />
        {t('workspaceDaemon.incompatible', { hostname: host?.hostname ?? '', reason: reason ?? '' })}
      </p>
    )
  }
  // Unknown is not absent: the backend could not tell, so no "start it" instructions.
  if (state === 'unknown') {
    return (
      <p role="status" className="flex items-center gap-2 text-xs text-amber-300">
        <span className="status-dot bg-amber-500" aria-hidden="true" />
        {t('workspaceDaemon.unknown', { reason: reason ?? '' })}
      </p>
    )
  }
  if (connected === false) return <WorkspaceDaemonHelp role="status" platform={fallbackPlatform} />
  if (error) {
    return (
      <p role="status" className="text-xs text-gray-400">
        {t('workspaceDaemon.statusError', { message: errorDetail(error) })}
      </p>
    )
  }
  return null
}
