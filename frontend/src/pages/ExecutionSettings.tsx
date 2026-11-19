import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Box, Globe, RotateCcw, TriangleAlert } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import {
  ENV_NAME, HOST_NAME, SANDBOX_MODES, useResearch, useResetCache, useSandbox, useSetResearch, useSetSandbox,
  type ResearchSetting, type SandboxMode, type SandboxSetting,
} from '../services/executionSettings'
import { useAppStore } from '../store/useAppStore'
import { FormActions, LoadError, LoadingRows, PageHeader, Panel, SavedNote, Spinner } from '../components/ui'

/** The sandbox limits shown, in order. */
const LIMITS = ['image', 'network', 'cpus', 'memory_mb'] as const

/** One name or host per line (commas also split), trimmed, blanks and repeats dropped. */
export function splitList(text: string): string[] {
  return [...new Set(text.split(/[\n,]/).map(item => item.trim()).filter(Boolean))]
}

/**
 * Execution (`/company/execution`): where the company's agent-written code
 * runs and what the research tools may read. Every member sees the settings
 * in force; a Manager changes them and resets the dependency cache.
 */
export default function ExecutionSettings() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const sandbox = useSandbox(companyId)
  const research = useResearch(companyId)

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader title={t('execution.title')} subtitle={t('execution.subtitle')} />
      {sandbox.isLoading ? <LoadingRows rows={1} label={t('execution.sandbox.loading')} />
        : sandbox.isError ? <LoadError onRetry={() => sandbox.refetch()}>{errorDetail(sandbox.error)}</LoadError>
          : sandbox.data && <SandboxPanel key={JSON.stringify([sandbox.data.mode, sandbox.data.env])} setting={sandbox.data} canManage={canManage} />}
      {research.isLoading ? <LoadingRows rows={1} label={t('execution.research.loading')} />
        : research.isError ? <LoadError onRetry={() => research.refetch()}>{errorDetail(research.error)}</LoadError>
          : research.data && <ResearchPanel key={research.data.hosts.join(',')} setting={research.data} canManage={canManage} />}
    </div>
  )
}

function SandboxPanel({ setting, canManage }: { setting: SandboxSetting; canManage: boolean }) {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const save = useSetSandbox(companyId)
  const reset = useResetCache(companyId)
  const ids = { mode: useId(), env: useId(), hint: useId() }
  const [mode, setMode] = useState<SandboxMode | ''>(setting.mode ?? '')
  const [envText, setEnvText] = useState(setting.env.join('\n'))
  const [confirmReset, setConfirmReset] = useState(false)
  const names = splitList(envText)
  const invalid = names.filter(name => !ENV_NAME.test(name))

  return (
    <Panel title={<span className="flex items-center gap-1.5"><Box size={13} aria-hidden="true" className="text-gray-400" />{t('execution.sandbox.title')}</span>}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-300">
          {t('execution.sandbox.inForce', { mode: t(`execution.sandbox.modes.${setting.effective_mode}`) })}
          {' '}{setting.mode === null && t('execution.sandbox.fromDeployment')}
        </p>
        {setting.effective_mode === 'host' && (
          <p role="note" className="flex items-start gap-1.5 text-xs text-amber-300">
            <TriangleAlert size={13} aria-hidden="true" className="mt-px flex-shrink-0" />{t('execution.sandbox.hostWarning')}
          </p>
        )}
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-4">
          {LIMITS.map(key => (
            <div key={key}>
              <dt className="text-meta">{t(`execution.sandbox.limits.${key}`)}</dt>
              <dd className="break-all tabular-nums text-gray-200">{setting.limits[key] === null || setting.limits[key] === undefined ? '—' : String(setting.limits[key])}</dd>
            </div>
          ))}
        </dl>
        {canManage ? (
          <form className="space-y-3" aria-label={t('execution.sandbox.title')}
            onSubmit={event => { event.preventDefault(); if (invalid.length === 0) save.mutate({ mode: mode || null, env: names }) }}>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label htmlFor={ids.mode} className="input-label">{t('execution.sandbox.mode')}</label>
                <select id={ids.mode} value={mode} onChange={event => setMode(event.target.value as SandboxMode | '')} className="select w-full">
                  <option value="">{t('execution.sandbox.default', { mode: t(`execution.sandbox.modes.${setting.default_mode}`) })}</option>
                  {SANDBOX_MODES.map(value => <option key={value} value={value}>{t(`execution.sandbox.modes.${value}`)}</option>)}
                </select>
              </div>
              <div>
                <label htmlFor={ids.env} className="input-label">{t('execution.sandbox.env')}</label>
                <textarea id={ids.env} rows={3} value={envText} spellCheck={false} onChange={event => setEnvText(event.target.value)}
                  aria-invalid={invalid.length > 0} aria-describedby={ids.hint} className="input w-full font-mono text-xs" />
                <p id={ids.hint} className={`text-meta ${invalid.length > 0 ? 'text-red-300' : ''}`}>
                  {invalid.length > 0 ? t('execution.sandbox.envInvalid', { names: invalid.join(', ') }) : t('execution.sandbox.envHint')}
                </p>
              </div>
            </div>
            <FormActions>
              <button type="submit" className="btn-tool border border-surface-700" disabled={save.isPending || invalid.length > 0}>
                {save.isPending && <Spinner size={13} />}{t('common.save')}
              </button>
              {save.isSuccess && <SavedNote>{t('execution.saved')}</SavedNote>}
              {save.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(save.error)}</span>}
            </FormActions>
          </form>
        ) : (
          <p className="text-xs text-gray-400">
            {setting.env.length > 0 ? t('execution.sandbox.envList', { names: setting.env.join(', ') }) : t('execution.sandbox.envNone')}
            {' '}{t('execution.managersChange')}
          </p>
        )}
        {canManage && (
          <div className="space-y-2 border-t border-surface-700 pt-3">
            <p className="text-xs text-gray-400">{t('execution.cache.hint')}</p>
            {confirmReset ? (
              <span role="group" aria-label={t('execution.cache.confirmLabel')} className="inline-flex flex-wrap items-center gap-1">
                <span className="text-xs text-amber-300">{t('execution.cache.confirm')}</span>
                <button type="button" className="btn-tool border border-amber-700/60 text-amber-200" disabled={reset.isPending}
                  onClick={() => reset.mutate(undefined, { onSettled: () => setConfirmReset(false) })}>
                  {reset.isPending && <Spinner size={13} />}{t('execution.cache.resetConfirm')}
                </button>
                <button type="button" className="btn-tool" onClick={() => setConfirmReset(false)}>{t('common.cancel')}</button>
              </span>
            ) : (
              <button type="button" className="btn-tool border border-surface-700" onClick={() => setConfirmReset(true)}>
                <RotateCcw size={13} aria-hidden="true" />{t('execution.cache.reset')}
              </button>
            )}
            {reset.isSuccess && (
              <SavedNote>{reset.data.quarantine_cleared ? t('execution.cache.doneQuarantine') : t('execution.cache.done')}</SavedNote>
            )}
            {reset.isError && <p role="alert" className="text-xs text-red-300">{errorDetail(reset.error)}</p>}
          </div>
        )}
      </div>
    </Panel>
  )
}

function ResearchPanel({ setting, canManage }: { setting: ResearchSetting; canManage: boolean }) {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const save = useSetResearch(companyId)
  const ids = { hosts: useId(), hint: useId() }
  const [text, setText] = useState(setting.hosts.join('\n'))
  const hosts = splitList(text.toLowerCase())
  const invalid = hosts.filter(host => !HOST_NAME.test(host))

  return (
    <Panel title={<span className="flex items-center gap-1.5"><Globe size={13} aria-hidden="true" className="text-gray-400" />{t('execution.research.title')}</span>}>
      <div className="space-y-3 p-3">
        <p className="text-xs text-gray-300">
          {setting.enabled ? t('execution.research.on', { roles: setting.roles.join(', ') || '—' }) : t('execution.research.off')}
        </p>
        <div>
          <p className="text-meta">{t('execution.research.defaults')}</p>
          <ul className="mt-1 flex flex-wrap gap-1" aria-label={t('execution.research.defaults')}>
            {setting.default_hosts.map(host => <li key={host}><code className="chip font-mono">{host}</code></li>)}
          </ul>
        </div>
        {canManage ? (
          <form className="space-y-3" aria-label={t('execution.research.title')}
            onSubmit={event => { event.preventDefault(); if (invalid.length === 0) save.mutate(hosts) }}>
            <div>
              <label htmlFor={ids.hosts} className="input-label">{t('execution.research.own')}</label>
              <textarea id={ids.hosts} rows={3} value={text} spellCheck={false} onChange={event => setText(event.target.value)}
                aria-invalid={invalid.length > 0} aria-describedby={ids.hint} className="input w-full font-mono text-xs" />
              <p id={ids.hint} className={`text-meta ${invalid.length > 0 ? 'text-red-300' : ''}`}>
                {invalid.length > 0 ? t('execution.research.invalid', { hosts: invalid.join(', ') }) : t('execution.research.hint')}
              </p>
            </div>
            <FormActions>
              <button type="submit" className="btn-tool border border-surface-700" disabled={save.isPending || invalid.length > 0}>
                {save.isPending && <Spinner size={13} />}{t('common.save')}
              </button>
              {save.isSuccess && <SavedNote>{t('execution.saved')}</SavedNote>}
              {save.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(save.error)}</span>}
            </FormActions>
          </form>
        ) : (
          <p className="text-xs text-gray-400">
            {setting.hosts.length > 0 ? t('execution.research.ownList', { hosts: setting.hosts.join(', ') }) : t('execution.research.ownNone')}
            {' '}{t('execution.managersChange')}
          </p>
        )}
      </div>
    </Panel>
  )
}
