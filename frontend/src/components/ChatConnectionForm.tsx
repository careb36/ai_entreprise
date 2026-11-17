import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CircleAlert, CircleCheck, FlaskConical, Loader2, Save, Trash2 } from 'lucide-react'
import { useDeleteChat, useSaveChat, useTestChat } from '../hooks/useChat'
import { errorDetail, type ChatConnection, type ChatConnectionInput, type ChatProvider } from '../services/api'
import { Panel } from './ui'

interface Props {
  companyId: number
  provider: ChatProvider
  initial: ChatConnection | null
}

/**
 * One chat platform's connection, for an Owner. The posting credential and
 * the signing secret are write-only: the backend says only whether each is
 * set, and an empty field keeps the stored one.
 */
export default function ChatConnectionForm({ companyId, provider, initial }: Props) {
  const { t } = useTranslation()
  const save = useSaveChat(companyId, provider)
  const test = useTestChat(companyId, provider)
  const remove = useDeleteChat(companyId, provider)
  const [form, setForm] = useState({
    workspace_id: initial?.workspace_id ?? '',
    channel: initial?.channel ?? '',
    post_credential: '',
    signing_secret: '',
    enabled: initial?.enabled ?? true,
  })
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
    setForm(current => ({ ...current, [key]: value }))
  const p = `chat.${provider}`

  function submit() {
    const data: ChatConnectionInput = {
      workspace_id: form.workspace_id.trim(),
      channel: provider === 'slack' ? form.channel.trim() : null,
      enabled: form.enabled,
    }
    if (form.post_credential) data.post_credential = form.post_credential
    if (form.signing_secret) data.signing_secret = form.signing_secret
    save.mutate(data)
  }

  const secretPlaceholder = (isSet: boolean | undefined) =>
    isSet ? t('chat.secretSet') : t('chat.secretUnset')
  const result = test.data
  const spinner = <Loader2 size={13} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
  return (
    <Panel title={t(`${p}.title`)}>
      <form onSubmit={e => { e.preventDefault(); submit() }} className="space-y-3 p-3" aria-label={t(`${p}.title`)}>
        <p className="text-xs text-gray-400">{t(`${p}.help`)}</p>
        {initial && (
          <p className="text-xs text-gray-400">
            {t('chat.inboundUrl')}{' '}
            <code className="break-all rounded bg-surface-950/60 px-1 py-0.5 font-mono text-gray-200">{initial.inbound_url}</code>
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="input-label">{t(`${p}.workspace`)}</span>
            <input className="input w-full" required value={form.workspace_id} onChange={e => set('workspace_id', e.target.value)} />
          </label>
          {provider === 'slack' && (
            <label className="block">
              <span className="input-label">{t('chat.slack.channel')}</span>
              <input className="input w-full" required value={form.channel} onChange={e => set('channel', e.target.value)} />
            </label>
          )}
          <label className="block">
            <span className="input-label">{t(`${p}.postCredential`)}</span>
            <input className="input w-full" type="password" autoComplete="new-password" value={form.post_credential}
              onChange={e => set('post_credential', e.target.value)} placeholder={secretPlaceholder(initial?.has_post_credential)} />
          </label>
          <label className="block">
            <span className="input-label">{t(`${p}.signingSecret`)}</span>
            <input className="input w-full" type="password" autoComplete="new-password" value={form.signing_secret}
              onChange={e => set('signing_secret', e.target.value)} placeholder={secretPlaceholder(initial?.has_signing_secret)} />
          </label>
        </div>
        <label className="flex items-center gap-2 text-[13px] text-gray-200">
          <input type="checkbox" className="h-4 w-4 flex-shrink-0 rounded accent-brand-500" checked={form.enabled} onChange={e => set('enabled', e.target.checked)} />
          {t('chat.enabled')}
        </label>
        {save.isError && <div role="alert" className="alert-error text-sm">{t('chat.saveError', { message: errorDetail(save.error) })}</div>}
        {test.isError && <div role="alert" className="alert-error text-sm">{errorDetail(test.error)}</div>}
        {result && (
          <div role="status" className={`${result.ok ? 'alert-success' : 'alert-error'} items-center text-sm`}>
            {result.ok
              ? <CircleCheck size={15} aria-hidden="true" className="flex-shrink-0" />
              : <CircleAlert size={15} aria-hidden="true" className="flex-shrink-0" />}
            {result.ok ? t('chat.testOk') : t('chat.testFailed', { detail: result.detail })}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2 border-t border-surface-700 pt-3">
          <button type="submit" className="btn-primary btn-sm" disabled={save.isPending}>
            {save.isPending ? spinner : <Save size={13} aria-hidden="true" />}
            {t('chat.save')}
          </button>
          {initial && (
            <>
              <button type="button" className="btn-tool border border-surface-700" disabled={test.isPending} onClick={() => test.mutate()}>
                {test.isPending ? spinner : <FlaskConical size={13} aria-hidden="true" />}
                {t('chat.test')}
              </button>
              <button type="button" className="btn-tool border border-surface-700 hover:text-red-300" disabled={remove.isPending}
                onClick={() => { if (window.confirm(t('chat.deleteConfirm'))) remove.mutate() }}>
                <Trash2 size={13} aria-hidden="true" />
                {t('chat.delete')}
              </button>
            </>
          )}
          {save.isSuccess && (
            <span role="status" className="inline-flex items-center gap-1 text-xs text-emerald-300">
              <CircleCheck size={13} aria-hidden="true" />{t('chat.saved')}
            </span>
          )}
        </div>
      </form>
    </Panel>
  )
}
