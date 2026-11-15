import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import { useDeleteChat, useSaveChat, useTestChat } from '../hooks/useChat'
import { errorDetail, type ChatConnection, type ChatConnectionInput, type ChatProvider } from '../services/api'

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
  return (
    <form onSubmit={e => { e.preventDefault(); submit() }} className="card p-4 space-y-4" aria-label={t(`${p}.title`)}>
      <h2 className="text-lg font-semibold">{t(`${p}.title`)}</h2>
      <p className="text-xs text-gray-400">{t(`${p}.help`)}</p>
      {initial && (
        <p className="text-xs text-gray-400">
          {t('chat.inboundUrl')}{' '}
          <code className="text-gray-200 break-all">{initial.inbound_url}</code>
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
      <label className="flex items-center gap-2 text-sm text-gray-300">
        <input type="checkbox" checked={form.enabled} onChange={e => set('enabled', e.target.checked)} />
        {t('chat.enabled')}
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn btn-primary" disabled={save.isPending}>
          {save.isPending && <Loader2 size={15} className="animate-spin" />}
          {t('chat.save')}
        </button>
        {initial && (
          <>
            <button type="button" className="btn btn-secondary" disabled={test.isPending} onClick={() => test.mutate()}>
              {test.isPending && <Loader2 size={15} className="animate-spin" />}
              {t('chat.test')}
            </button>
            <button type="button" className="btn btn-secondary" disabled={remove.isPending}
              onClick={() => { if (window.confirm(t('chat.deleteConfirm'))) remove.mutate() }}>
              {t('chat.delete')}
            </button>
          </>
        )}
        {save.isSuccess && <span role="status" className="text-sm text-status-success">{t('chat.saved')}</span>}
      </div>
      {save.isError && <div role="alert" className="alert-error text-sm">{t('chat.saveError', { message: errorDetail(save.error) })}</div>}
      {test.isError && <div role="alert" className="alert-error text-sm">{errorDetail(test.error)}</div>}
      {result && (
        <div role="status" className={result.ok ? 'text-sm text-status-success' : 'alert-error text-sm'}>
          {result.ok ? t('chat.testOk') : t('chat.testFailed', { detail: result.detail })}
        </div>
      )}
    </form>
  )
}
