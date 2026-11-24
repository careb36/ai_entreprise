import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Layers, Users } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import CompanyExportPanel from '../components/CompanyExportPanel'
import { errorDetail } from '../services/api'
import {
  COMMAND_FIELDS, TEAM_SELECTIONS, useSetStackProfile, useSetTeamSelection, useStackProfile, useTeamSelection,
  type CommandField, type StackOverrides, type StackProfileSetting, type TeamSelection,
} from '../services/projectSettings'
import { useAppStore } from '../store/useAppStore'
import { FormActions, LoadError, LoadingRows, PageHeader, Panel, SavedNote, Spinner } from '../components/ui'

/** How one command field is set: left to the detection, a command, or turned off. */
type CommandChoice = { mode: 'detected' | 'command' | 'off'; value: string }

export function choiceOf(value: string | undefined): CommandChoice {
  if (value === undefined) return { mode: 'detected', value: '' }
  return value === '' ? { mode: 'off', value: '' } : { mode: 'command', value }
}

/** The overrides to send: the root's fields from the form, the folders, every-delivery commands and declared programs kept as stored. */
export function overridesFrom(
  stored: StackOverrides,
  form: { stack: string; port: string; probePath: string; web: '' | 'yes' | 'no'; commands: Record<CommandField, CommandChoice> },
): StackOverrides {
  const out: StackOverrides = {}
  if (stored.folders) out.folders = stored.folders
  if (stored.every_delivery) out.every_delivery = stored.every_delivery
  if (stored.programs) out.programs = stored.programs
  if (form.stack) out.stack = form.stack
  if (form.port.trim()) out.port = Number(form.port)
  if (form.probePath.trim()) out.probe_path = form.probePath.trim()
  if (form.web) out.web = form.web === 'yes'
  for (const field of COMMAND_FIELDS) {
    const choice = form.commands[field]
    if (choice.mode === 'off') out[field] = ''
    else if (choice.mode === 'command' && choice.value.trim()) out[field] = choice.value.trim()
  }
  return out
}

/**
 * Project settings (`/company/project`): how the factory builds and tests the
 * project (the stack profile's overrides of the detection) and how a
 * sprint's team is chosen. Every member reads them; a Manager changes them.
 * The Owner also exports the company's configuration as a file here.
 */
export default function ProjectSettings() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const stack = useStackProfile(companyId)

  return (
    <div className="page-container max-w-4xl space-y-4">
      <PageHeader title={t('projectSettings.title')} subtitle={t('projectSettings.subtitle')} />
      <TeamSelectionPanel canManage={canManage} />
      {stack.isLoading ? <LoadingRows rows={2} label={t('projectSettings.stack.loading')} />
        : stack.isError ? <LoadError onRetry={() => stack.refetch()}>{errorDetail(stack.error)}</LoadError>
          : stack.data && <StackPanel key={JSON.stringify(stack.data.overrides)} setting={stack.data} canManage={canManage} />}
      {role === 'Owner' && <CompanyExportPanel companyId={companyId} />}
    </div>
  )
}

function TeamSelectionPanel({ canManage }: { canManage: boolean }) {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const query = useTeamSelection(companyId)
  const save = useSetTeamSelection(companyId)
  const current = query.data?.team_selection

  return (
    <Panel title={<span className="flex items-center gap-1.5"><Users size={13} aria-hidden="true" className="text-gray-400" />{t('projectSettings.team.title')}</span>}>
      <div className="space-y-2 p-3">
        {query.isLoading && <LoadingRows rows={1} label={t('projectSettings.team.loading')} />}
        {query.isError && <LoadError onRetry={() => query.refetch()}>{errorDetail(query.error)}</LoadError>}
        {current && (canManage ? (
          <fieldset className="space-y-2">
            <legend className="sr-only">{t('projectSettings.team.title')}</legend>
            {TEAM_SELECTIONS.map(value => (
              <label key={value} className="flex items-start gap-2 text-xs text-gray-200">
                <input type="radio" name="team-selection" className="mt-0.5 accent-brand-500" checked={current === value} disabled={save.isPending}
                  onChange={() => save.mutate(value as TeamSelection)} />
                <span><span className="font-medium">{t(`projectSettings.team.${value}.label`)}</span><span className="block text-gray-400">{t(`projectSettings.team.${value}.hint`)}</span></span>
              </label>
            ))}
            {save.isSuccess && <SavedNote>{t('projectSettings.saved')}</SavedNote>}
            {save.isError && <p role="alert" className="text-xs text-red-300">{errorDetail(save.error)}</p>}
          </fieldset>
        ) : (
          <p className="text-xs text-gray-300">{t(`projectSettings.team.${current}.label`)} — {t(`projectSettings.team.${current}.hint`)}</p>
        ))}
      </div>
    </Panel>
  )
}

function StackPanel({ setting, canManage }: { setting: StackProfileSetting; canManage: boolean }) {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const save = useSetStackProfile(companyId)
  const stored = setting.overrides
  const ids = { stack: useId(), port: useId(), probe: useId(), web: useId() }
  const [stack, setStack] = useState(stored.stack ?? '')
  const [port, setPort] = useState(stored.port === undefined ? '' : String(stored.port))
  const [probePath, setProbePath] = useState(stored.probe_path ?? '')
  const [web, setWeb] = useState<'' | 'yes' | 'no'>(stored.web === undefined ? '' : stored.web ? 'yes' : 'no')
  const [commands, setCommands] = useState<Record<CommandField, CommandChoice>>(
    () => Object.fromEntries(COMMAND_FIELDS.map(field => [field, choiceOf(stored[field])])) as Record<CommandField, CommandChoice>,
  )
  const portBad = port.trim() !== '' && !(Number.isInteger(Number(port)) && Number(port) >= 1 && Number(port) <= 65535)
  const probeBad = probePath.trim() !== '' && !probePath.trim().startsWith('/')
  const folders = Object.keys(stored.folders ?? {})

  return (
    <Panel title={<span className="flex items-center gap-1.5"><Layers size={13} aria-hidden="true" className="text-gray-400" />{t('projectSettings.stack.title')}</span>}>
      <form className="space-y-3 p-3" aria-label={t('projectSettings.stack.title')}
        onSubmit={event => {
          event.preventDefault()
          if (portBad || probeBad) return
          save.mutate(overridesFrom(stored, { stack, port, probePath, web, commands }))
        }}>
        <p className="text-xs text-gray-400">{t('projectSettings.stack.hint')}</p>
        <fieldset disabled={!canManage} className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-4">
            <div>
              <label htmlFor={ids.stack} className="input-label">{t('projectSettings.stack.stack')}</label>
              <select id={ids.stack} value={stack} onChange={event => setStack(event.target.value)} className="select w-full">
                <option value="">{t('projectSettings.stack.detected')}</option>
                {setting.stacks.map(s => <option key={s.stack} value={s.stack}>{s.stack}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor={ids.port} className="input-label">{t('projectSettings.stack.port')}</label>
              <input id={ids.port} inputMode="numeric" value={port} onChange={event => setPort(event.target.value)} aria-invalid={portBad}
                placeholder={t('projectSettings.stack.detected')} className="input w-full" />
            </div>
            <div>
              <label htmlFor={ids.probe} className="input-label">{t('projectSettings.stack.probePath')}</label>
              <input id={ids.probe} value={probePath} onChange={event => setProbePath(event.target.value)} aria-invalid={probeBad}
                placeholder={t('projectSettings.stack.detected')} className="input w-full font-mono" />
            </div>
            <div>
              <label htmlFor={ids.web} className="input-label">{t('projectSettings.stack.web')}</label>
              <select id={ids.web} value={web} onChange={event => setWeb(event.target.value as '' | 'yes' | 'no')} className="select w-full">
                <option value="">{t('projectSettings.stack.detected')}</option>
                <option value="yes">{t('projectSettings.stack.webYes')}</option>
                <option value="no">{t('projectSettings.stack.webNo')}</option>
              </select>
            </div>
          </div>
          {(portBad || probeBad) && <p role="alert" className="text-xs text-red-300">{portBad ? t('projectSettings.stack.portBad') : t('projectSettings.stack.probeBad')}</p>}
          <div className="space-y-2">
            {COMMAND_FIELDS.map(field => (
              <CommandRow key={field} field={field} choice={commands[field]}
                onChange={choice => setCommands(current => ({ ...current, [field]: choice }))} />
            ))}
          </div>
        </fieldset>
        {folders.length > 0 && <p className="text-meta">{t('projectSettings.stack.folders', { folders: folders.join(', ') })}</p>}
        {Object.keys(setting.installed_manifests).length > 0 && (
          <p className="text-meta">{t('projectSettings.stack.installed', { manifests: Object.keys(setting.installed_manifests).join(', ') })}</p>
        )}
        {canManage ? (
          <FormActions>
            <button type="submit" className="btn-tool border border-surface-700" disabled={save.isPending || portBad || probeBad}>
              {save.isPending && <Spinner size={13} />}{t('common.save')}
            </button>
            {save.isSuccess && <SavedNote>{t('projectSettings.saved')}</SavedNote>}
            {save.isError && <span role="alert" className="text-xs text-red-300">{errorDetail(save.error)}</span>}
          </FormActions>
        ) : <p className="text-meta">{t('projectSettings.managersChange')}</p>}
      </form>
    </Panel>
  )
}

function CommandRow({ field, choice, onChange }: { field: CommandField; choice: CommandChoice; onChange: (choice: CommandChoice) => void }) {
  const { t } = useTranslation()
  const ids = { mode: useId(), value: useId() }
  return (
    <div className="grid items-end gap-2 sm:grid-cols-[8rem_10rem_1fr]">
      <span className="text-xs font-medium text-gray-200">{t(`projectSettings.stack.commands.${field}`)}</span>
      <div>
        <label htmlFor={ids.mode} className="sr-only">{t('projectSettings.stack.modeFor', { step: t(`projectSettings.stack.commands.${field}`) })}</label>
        <select id={ids.mode} value={choice.mode} onChange={event => onChange({ ...choice, mode: event.target.value as CommandChoice['mode'] })} className="select control-sm w-full">
          <option value="detected">{t('projectSettings.stack.detected')}</option>
          <option value="command">{t('projectSettings.stack.command')}</option>
          <option value="off">{t('projectSettings.stack.off')}</option>
        </select>
      </div>
      <div>
        <label htmlFor={ids.value} className="sr-only">{t('projectSettings.stack.commandFor', { step: t(`projectSettings.stack.commands.${field}`) })}</label>
        <input id={ids.value} value={choice.value} disabled={choice.mode !== 'command'} maxLength={500} spellCheck={false}
          onChange={event => onChange({ ...choice, value: event.target.value })} className="input control-sm w-full font-mono" />
      </div>
    </div>
  )
}
