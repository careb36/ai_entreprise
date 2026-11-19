import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Ban, CircleCheck, CirclePause, Plug, Trash2, Wrench, type LucideIcon } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import { parseServersMap, useMcpChange, useMcpServers, useMcpTools, type McpServer } from '../services/mcpServers'
import { useAppStore } from '../store/useAppStore'
import { CountPill, EmptyState, FormActions, LoadError, LoadingRows, PageHeader, Panel, SavedNote, Spinner, StatusBadge, type StatusTone } from '../components/ui'

type ServerState = 'enabled' | 'disabled' | 'refused'
const STATE_LOOK: Record<ServerState, { icon: LucideIcon; tone: StatusTone }> = {
  enabled: { icon: CircleCheck, tone: 'success' },
  disabled: { icon: CirclePause, tone: 'neutral' },
  refused: { icon: Ban, tone: 'warning' },
}

export function serverState(server: McpServer): ServerState {
  if (server.start_refused) return 'refused'
  return server.enabled ? 'enabled' : 'disabled'
}

/**
 * MCP servers (`/company/mcp`): the tools the company's agents may reach.
 * Every member sees each server, the roles it serves and its secrets by name;
 * a Manager adds servers from an `mcpServers` map, enables or disables one,
 * scopes it to roles, lists its tools (which starts it, audited) and removes
 * it after a confirmation. The operator's policy may refuse a start: that
 * reason is shown, and the backend refuses enabling such a server.
 */
export default function McpServers() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const servers = useMcpServers(companyId)
  const rows = servers.data?.servers ?? []

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader title={t('mcp.title')} subtitle={t('mcp.subtitle')} />
      <Panel title={t('mcp.listTitle')} count={rows.length > 0 ? <CountPill count={rows.length} label={t('mcp.count', { count: rows.length })} /> : undefined}>
        {servers.isLoading ? (
          <div className="p-3"><LoadingRows rows={2} label={t('mcp.loading')} /></div>
        ) : servers.isError ? (
          <div className="p-3"><LoadError onRetry={() => servers.refetch()}>{t('mcp.loadError', { message: errorDetail(servers.error) })}</LoadError></div>
        ) : rows.length === 0 ? (
          <EmptyState icon={Plug} title={t('mcp.empty')} />
        ) : (
          <ul className="data-list" aria-label={t('mcp.listTitle')}>
            {rows.map(server => <ServerRow key={server.id} server={server} canManage={canManage} />)}
          </ul>
        )}
      </Panel>
      {canManage && <AddServers />}
    </div>
  )
}

function ServerRow({ server, canManage }: { server: McpServer; canManage: boolean }) {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const change = useMcpChange(companyId)
  const tools = useMcpTools()
  const rolesId = useId()
  const [roles, setRoles] = useState(server.roles.join(', '))
  const [confirming, setConfirming] = useState(false)
  const state = serverState(server)
  const look = STATE_LOOK[state]
  const target = server.transport === 'stdio' ? [server.command, ...server.args].filter(Boolean).join(' ') : server.url

  return (
    <li className="data-row space-y-2 px-3 py-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-medium text-gray-100">{server.name}</span>
        <code className="chip font-mono">{server.transport}</code>
        <StatusBadge icon={look.icon} tone={look.tone} label={t(`mcp.state.${state}`)} />
      </div>
      {target && <code className="block break-all font-mono text-[11px] text-gray-300">{target}</code>}
      {server.description && <p className="text-gray-300">{server.description}</p>}
      {server.start_refused && <p role="note" className="text-amber-300">{t('mcp.refused', { reason: server.start_refused })}</p>}
      <p className="text-gray-400">
        {server.roles.length > 0 ? t('mcp.roles', { roles: server.roles.join(', ') }) : t('mcp.allRoles')}
        {' · '}
        {server.secrets_readable
          ? (server.env_keys.length + server.header_names.length > 0 ? t('mcp.secrets', { names: [...server.env_keys, ...server.header_names].join(', ') }) : t('mcp.noSecrets'))
          : t('mcp.secretsUnreadable')}
      </p>
      {canManage && (
        <div className="flex flex-wrap items-end gap-2">
          <button type="button" className="btn-tool border border-surface-700" disabled={change.isPending || (!server.enabled && Boolean(server.start_refused))}
            onClick={() => change.mutate({ kind: 'patch', serverId: server.id, patch: { enabled: !server.enabled } })}>
            {server.enabled ? t('mcp.disable') : t('mcp.enable')}
          </button>
          <form className="flex flex-wrap items-end gap-1" aria-label={t('mcp.rolesFormLabel', { name: server.name })}
            onSubmit={event => {
              event.preventDefault()
              change.mutate({ kind: 'patch', serverId: server.id, patch: { roles: roles.split(',').map(r => r.trim()).filter(Boolean) } })
            }}>
            <div>
              <label htmlFor={rolesId} className="input-label">{t('mcp.rolesLabel')}</label>
              <input id={rolesId} value={roles} onChange={event => setRoles(event.target.value)} placeholder={t('mcp.rolesPlaceholder')} className="input control-sm w-56" />
            </div>
            <button type="submit" className="btn-tool" disabled={change.isPending}>{t('mcp.saveRoles')}</button>
          </form>
          <button type="button" className="btn-tool" disabled={tools.isPending || Boolean(server.start_refused)} onClick={() => tools.mutate(server.id)}>
            {tools.isPending ? <Spinner size={13} /> : <Wrench size={13} aria-hidden="true" />}{t('mcp.listTools')}
          </button>
          {confirming ? (
            <span role="group" aria-label={t('mcp.confirmLabel', { name: server.name })} className="inline-flex flex-wrap items-center gap-1">
              <span className="text-amber-300">{t('mcp.confirmRemove', { name: server.name })}</span>
              <button type="button" className="btn-tool border border-red-700/60 text-red-300" disabled={change.isPending}
                onClick={() => change.mutate({ kind: 'remove', serverId: server.id }, { onSettled: () => setConfirming(false) })}>
                {t('mcp.removeConfirm')}
              </button>
              <button type="button" className="btn-tool" onClick={() => setConfirming(false)}>{t('common.cancel')}</button>
            </span>
          ) : (
            <button type="button" className="btn-tool hover:text-red-300" aria-label={t('mcp.removeNamed', { name: server.name })} onClick={() => setConfirming(true)}>
              <Trash2 size={13} aria-hidden="true" />{t('mcp.remove')}
            </button>
          )}
        </div>
      )}
      {canManage && <p className="text-meta">{t('mcp.toolsHint')}</p>}
      {tools.data && (
        <ul className="space-y-0.5 rounded-md border border-surface-700 p-2" aria-label={t('mcp.toolsOf', { name: server.name })}>
          {tools.data.tools.length === 0 ? <li className="text-gray-400">{t('mcp.noTools')}</li> : tools.data.tools.map(tool => (
            <li key={tool.name}><code className="font-mono text-gray-200">{tool.name}</code>{tool.description ? <span className="text-gray-400"> — {tool.description}</span> : null}</li>
          ))}
        </ul>
      )}
      {change.isError && <p role="alert" className="text-red-300">{errorDetail(change.error)}</p>}
      {tools.isError && <p role="alert" className="text-red-300">{errorDetail(tools.error)}</p>}
    </li>
  )
}

/**
 * Add servers from the standard `mcpServers` JSON map. The text may carry
 * secrets (environment, headers): it is never stored by the browser, the
 * field is cleared after a save, and the backend returns only the names.
 */
function AddServers() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const change = useMcpChange(companyId)
  const ids = { field: useId(), hint: useId() }
  const [text, setText] = useState('')
  const [added, setAdded] = useState(false)
  const parsed = text.trim() ? parseServersMap(text) : null
  const problem = parsed && 'error' in parsed ? parsed.error : null

  return (
    <Panel title={t('mcp.addTitle')}>
      <form className="space-y-3 p-3" aria-label={t('mcp.addTitle')}
        onSubmit={event => {
          event.preventDefault()
          if (!parsed || !('map' in parsed)) return
          setAdded(false)
          change.mutate({ kind: 'add', map: parsed.map }, { onSuccess: () => { setText(''); setAdded(true) } })
        }}>
        <label htmlFor={ids.field} className="input-label">{t('mcp.addLabel')}</label>
        <textarea id={ids.field} rows={8} value={text} spellCheck={false} autoComplete="off" onChange={event => setText(event.target.value)}
          aria-invalid={Boolean(problem)} aria-describedby={ids.hint} placeholder={t('mcp.addPlaceholder')} className="input w-full font-mono text-xs" />
        <p id={ids.hint} className={`text-meta ${problem ? 'text-red-300' : ''}`}>{problem ? t(`mcp.parse.${problem}`) : t('mcp.addHint')}</p>
        <FormActions>
          <button type="submit" className="btn-tool border border-surface-700" disabled={change.isPending || !parsed || Boolean(problem)}>
            {change.isPending && <Spinner size={13} />}{t('mcp.add')}
          </button>
          {added && <SavedNote>{t('mcp.added')}</SavedNote>}
          {change.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(change.error)}</span>}
        </FormActions>
      </form>
    </Panel>
  )
}
