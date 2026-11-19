import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Copy, KeyRound, TriangleAlert, X } from 'lucide-react'
import { ApiError, errorDetail, type CompanyRole } from '../../services/api'
import { useCreateApiToken, type CreatedApiToken, type TokenRole } from '../../services/apiTokens'
import { useAppStore } from '../../store/useAppStore'
import { FormActions, Panel, SavedNote, Spinner } from '../../components/ui'

/**
 * Every token the UI makes expires: the backend accepts no expiry, the UI never
 * offers it (a service credential with a bounded lifetime, the strictest
 * reading of OWASP ASVS 4.0.3 V2.10).
 */
export const EXPIRY_DAYS = [30, 90, 180, 365] as const
const DEFAULT_DAYS = 90

/** The token roles a person may give: never above their own, never Owner. */
export function grantableRoles(ownRole: CompanyRole): TokenRole[] {
  return ownRole === 'Viewer' ? ['Viewer'] : ['Viewer', 'Manager']
}

/** Name, role and lifetime of a new token (Manager or Owner). */
export function CreateTokenForm({ ownRole, onCreated }: { ownRole: CompanyRole; onCreated: (token: CreatedApiToken) => void }) {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const create = useCreateApiToken(companyId)
  const ids = { name: useId(), role: useId(), expiry: useId() }
  const [name, setName] = useState('')
  const [role, setRole] = useState<TokenRole>('Viewer')
  const [days, setDays] = useState<number>(DEFAULT_DAYS)

  function submit() {
    const trimmed = name.trim()
    if (!trimmed || companyId === null) return
    create.mutate({ name: trimmed, role, expires_in_days: days }, {
      onSuccess: token => {
        setName('')
        onCreated(token)
      },
    })
  }

  const error = create.error instanceof ApiError && create.error.status === 403
    ? t('apiTokens.forbidden')
    : t('apiTokens.createError', { message: errorDetail(create.error) })

  return (
    <Panel title={t('apiTokens.createTitle')}>
      <form className="space-y-3 p-3" aria-label={t('apiTokens.createTitle')} onSubmit={event => { event.preventDefault(); submit() }}>
        <div className="grid gap-3 sm:grid-cols-[1fr_10rem_10rem]">
          <div>
            <label htmlFor={ids.name} className="input-label">{t('apiTokens.name')}</label>
            <input id={ids.name} required maxLength={80} value={name} autoComplete="off"
              onChange={event => setName(event.target.value)} placeholder={t('apiTokens.namePlaceholder')} className="input w-full" />
          </div>
          <div>
            <label htmlFor={ids.role} className="input-label">{t('apiTokens.role')}</label>
            <select id={ids.role} value={role} onChange={event => setRole(event.target.value as TokenRole)} className="select w-full">
              {grantableRoles(ownRole).map(value => <option key={value} value={value}>{t(`members.roles.${value}`)}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor={ids.expiry} className="input-label">{t('apiTokens.expiry')}</label>
            <select id={ids.expiry} value={days} onChange={event => setDays(Number(event.target.value))} className="select w-full">
              {EXPIRY_DAYS.map(value => <option key={value} value={value}>{t('apiTokens.days', { count: value })}</option>)}
            </select>
          </div>
        </div>
        <p className="text-meta">{t('apiTokens.createHint')}</p>
        <FormActions>
          <button type="submit" className="btn-primary" disabled={create.isPending || !name.trim()}>
            {create.isPending ? <Spinner size={13} /> : <KeyRound size={13} aria-hidden="true" />}
            {t('apiTokens.create')}
          </button>
          {create.isError && <span role="alert" className="text-xs text-red-300">{error}</span>}
        </FormActions>
      </form>
    </Panel>
  )
}

/**
 * The token just created, shown once. It lives only in this component's
 * props: nothing writes it to storage, and dismissing it drops it.
 */
export function NewTokenNotice({ token, onDismiss }: { token: CreatedApiToken; onDismiss: () => void }) {
  const { t } = useTranslation()
  const fieldId = useId()
  const [copied, setCopied] = useState(false)

  return (
    <section aria-labelledby={`${fieldId}-title`} className="panel border-amber-700/60 p-3">
      <div className="flex items-start gap-2">
        <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 flex-shrink-0 text-amber-300" />
        <div className="min-w-0 flex-1 space-y-2">
          <h2 id={`${fieldId}-title`} className="panel-title">{t('apiTokens.createdTitle', { name: token.name })}</h2>
          <p className="text-xs text-gray-300">{t('apiTokens.createdOnce')}</p>
          <label htmlFor={fieldId} className="input-label">{t('apiTokens.secretLabel')}</label>
          <div className="flex flex-wrap gap-2">
            <input id={fieldId} readOnly value={token.token} autoComplete="off" spellCheck={false}
              onFocus={event => event.currentTarget.select()} className="input min-w-0 flex-1 font-mono" />
            <button type="button" className="btn-tool border border-surface-700"
              onClick={() => { void navigator.clipboard?.writeText(token.token).then(() => setCopied(true), () => setCopied(false)) }}>
              <Copy size={13} aria-hidden="true" />{t('apiTokens.copy')}
            </button>
            {copied && <SavedNote>{t('apiTokens.copied')}</SavedNote>}
          </div>
        </div>
        <button type="button" className="btn-tool" onClick={onDismiss} aria-label={t('apiTokens.dismiss')}>
          <X size={14} aria-hidden="true" />
        </button>
      </div>
    </section>
  )
}
