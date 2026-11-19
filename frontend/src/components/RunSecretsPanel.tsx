import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound, Plus, Trash2 } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { errorDetail } from '../services/api'
import { RUN_SECRET_NAME, useAddRunSecret, useDeleteRunSecret, useRunSecrets, type RunSecret } from '../services/runSecrets'
import { CountPill, FormActions, LoadError, LoadingRows, Panel, SavedNote, Spinner } from './ui'

/**
 * A task's run secrets (issue #357): the names every member sees, and for a
 * Manager or an Owner a form to add one and a way to delete one. A value is
 * written once and never shown back: the field is a password field the
 * browser does not autofill or remember (OWASP ASVS 4.0.3 V8.2.1), cleared
 * after each save.
 */
export function RunSecretsPanel({ companyId, taskId }: { companyId: number; taskId: number }) {
  const { t } = useTranslation()
  const role = useCurrentRole()
  const canManage = role === 'Owner' || role === 'Manager'
  const secrets = useRunSecrets(taskId)
  const rows = secrets.data?.secrets ?? []

  return (
    <Panel
      title={<span className="flex items-center gap-1.5"><KeyRound size={13} aria-hidden="true" className="text-gray-400" />{t('runSecrets.title')}</span>}
      count={rows.length > 0 ? <CountPill count={rows.length} label={t('runSecrets.count', { count: rows.length })} /> : undefined}>
      <p className="text-meta border-b border-surface-700 px-3 py-2">{t('runSecrets.note')}</p>
      {secrets.isLoading ? (
        <div className="p-3"><LoadingRows rows={1} label={t('runSecrets.loading')} /></div>
      ) : secrets.isError ? (
        <div className="p-3"><LoadError onRetry={() => secrets.refetch()}>{t('runSecrets.loadError', { message: errorDetail(secrets.error) })}</LoadError></div>
      ) : rows.length === 0 ? (
        <p className="px-3 py-3 text-xs text-gray-400">{t('runSecrets.empty')}</p>
      ) : (
        <ul className="data-list" aria-label={t('runSecrets.listLabel')}>
          {rows.map(secret => <SecretRow key={secret.id} secret={secret} companyId={companyId} canManage={canManage} />)}
        </ul>
      )}
      {canManage && <AddSecretForm companyId={companyId} taskId={taskId} />}
    </Panel>
  )
}

function SecretRow({ secret, companyId, canManage }: { secret: RunSecret; companyId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const remove = useDeleteRunSecret(companyId, secret.task_id)
  const [confirming, setConfirming] = useState(false)

  return (
    <li className="data-row flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
      <code className="chip font-mono">{secret.name}</code>
      <span className="text-meta">{t('runSecrets.valueHidden')}</span>
      {canManage && (
        <span className="ml-auto inline-flex flex-wrap items-center gap-1">
          {confirming ? (
            <>
              <span className="text-amber-300">{t('runSecrets.confirmDelete', { name: secret.name })}</span>
              <button type="button" className="btn-tool border border-red-700/60 text-red-300" disabled={remove.isPending}
                onClick={() => remove.mutate(secret.id, { onSettled: () => setConfirming(false) })}>
                {remove.isPending && <Spinner size={13} />}{t('runSecrets.deleteConfirm')}
              </button>
              <button type="button" className="btn-tool" onClick={() => setConfirming(false)}>{t('common.cancel')}</button>
            </>
          ) : (
            <button type="button" className="btn-tool" onClick={() => setConfirming(true)}
              aria-label={t('runSecrets.deleteNamed', { name: secret.name })}>
              <Trash2 size={13} aria-hidden="true" />{t('runSecrets.delete')}
            </button>
          )}
        </span>
      )}
      {remove.isError && <span role="alert" className="w-full text-red-300">{errorDetail(remove.error)}</span>}
    </li>
  )
}

function AddSecretForm({ companyId, taskId }: { companyId: number; taskId: number }) {
  const { t } = useTranslation()
  const add = useAddRunSecret(companyId, taskId)
  const ids = { name: useId(), value: useId(), hint: useId() }
  const [name, setName] = useState('')
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState<string | null>(null)
  const nameValid = name === '' || RUN_SECRET_NAME.test(name)

  function submit() {
    if (!name || !value || !RUN_SECRET_NAME.test(name)) return
    setSaved(null)
    add.mutate({ name, value }, {
      onSuccess: secret => {
        setSaved(secret.name)
        setName('')
        setValue('')
      },
    })
  }

  return (
    <form className="space-y-3 border-t border-surface-700 p-3" aria-label={t('runSecrets.addTitle')}
      onSubmit={event => { event.preventDefault(); submit() }}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={ids.name} className="input-label">{t('runSecrets.name')}</label>
          <input id={ids.name} required maxLength={80} value={name} autoComplete="off" spellCheck={false}
            aria-invalid={!nameValid} aria-describedby={ids.hint}
            onChange={event => setName(event.target.value)} placeholder={t('runSecrets.namePlaceholder')} className="input w-full font-mono" />
        </div>
        <div>
          <label htmlFor={ids.value} className="input-label">{t('runSecrets.value')}</label>
          <input id={ids.value} type="password" required value={value} autoComplete="new-password" spellCheck={false}
            onChange={event => setValue(event.target.value)} className="input w-full" />
        </div>
      </div>
      <p id={ids.hint} className={`text-meta ${nameValid ? '' : 'text-red-300'}`}>{nameValid ? t('runSecrets.nameHint') : t('runSecrets.nameInvalid')}</p>
      <FormActions>
        <button type="submit" className="btn-tool border border-surface-700" disabled={add.isPending || !name || !value || !nameValid}>
          {add.isPending ? <Spinner size={13} /> : <Plus size={13} aria-hidden="true" />}{t('runSecrets.add')}
        </button>
        {saved && <SavedNote>{t('runSecrets.saved', { name: saved })}</SavedNote>}
        {add.isError && <span role="alert" className="text-xs text-red-300">{t('runSecrets.addError', { message: errorDetail(add.error) })}</span>}
      </FormActions>
    </form>
  )
}
