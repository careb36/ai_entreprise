import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CircleAlert, CircleCheck, FlaskConical, Save, Trash2 } from 'lucide-react'
import { useCurrentRole } from '../hooks/useQueries'
import { useDeleteSso, useSaveSso, useSsoSettings, useTestSso } from '../hooks/useSso'
import { errorDetail, type SsoConnection, type SsoConnectionInput, type SsoPreset } from '../services/api'
import { useAppStore } from '../store/useAppStore'
import { PageHeader, Panel } from '../components/ui'
import { SsoDomains } from '../components/SsoDomains'
import { AccessNotice, ButtonSpinner, FormActions, LoadError, LoadingRows, SavedNote } from './admin/AdminParts'

const PRESETS: SsoPreset[] = ['generic', 'google', 'entra']

/**
 * The company's single sign-on (OpenID Connect), for its Owners: the
 * provider, who may join, and whether password sign-in is still allowed.
 * The client secret is write-only: the backend says only whether one is set.
 */
export default function CompanySso() {
  const { t } = useTranslation()
  const companyId = useAppStore(state => state.companyId)
  const role = useCurrentRole()
  const settings = useSsoSettings(companyId, role === 'Owner')
  // Held here, not in the form: the form remounts when a save changes the
  // connection, and what the save or the test answered must stay on screen.
  const save = useSaveSso(companyId)
  const test = useTestSso(companyId)
  const remove = useDeleteSso(companyId)

  return (
    <div className="page-container max-w-3xl">
      <PageHeader
        title={t('sso.title', { defaultValue: 'Single sign-on' })}
        subtitle={t('sso.subtitle', { defaultValue: 'Let members sign in with your identity provider (OpenID Connect).' })}
      />
      {role !== 'Owner' ? (
        <AccessNotice>
          {t('sso.ownerOnly', { role: role ?? '—', defaultValue: 'Only owners manage single sign-on. Your role here is {{role}}.' })}
        </AccessNotice>
      ) : settings.isLoading ? (
        <LoadingRows rows={2} />
      ) : settings.isError ? (
        <LoadError message={errorDetail(settings.error)} onRetry={() => settings.refetch()} />
      ) : settings.data ? (
        <SsoForm
          key={settings.data.connection?.updated_at ?? 'new'}
          mutations={{ save, test, remove }}
          initial={settings.data.connection}
          redirectUri={settings.data.redirect_uri}
          ownerSignedIn={settings.data.owner_signed_in}
        />
      ) : null}
      {role === 'Owner' && !!settings.data?.domains?.length && <SsoDomains companyId={companyId} domains={settings.data.domains} />}
    </div>
  )
}

interface FormProps {
  mutations: {
    save: ReturnType<typeof useSaveSso>
    test: ReturnType<typeof useTestSso>
    remove: ReturnType<typeof useDeleteSso>
  }
  initial: SsoConnection | null
  redirectUri: string
  ownerSignedIn: boolean
}

function SsoForm({ mutations, initial, redirectUri, ownerSignedIn }: FormProps) {
  const { t } = useTranslation()
  const { save, test, remove } = mutations
  const [form, setForm] = useState({
    slug: initial?.slug ?? '',
    preset: initial?.preset ?? ('generic' as SsoPreset),
    tenant: initial?.tenant ?? '',
    issuer: initial?.issuer ?? '',
    client_id: initial?.client_id ?? '',
    client_secret: '',
    domains: (initial?.allowed_domains ?? []).join(', '),
    default_role: initial?.default_role ?? ('Viewer' as const),
    auto_join: initial?.auto_join ?? false,
    enabled: initial?.enabled ?? false,
    require_sso: initial?.require_sso ?? false,
  })
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
    setForm(current => ({ ...current, [key]: value }))

  function submit() {
    const data: SsoConnectionInput = {
      slug: form.slug.trim(),
      preset: form.preset,
      tenant: form.preset === 'entra' ? form.tenant.trim() : null,
      issuer: form.preset === 'generic' ? form.issuer.trim() : null,
      client_id: form.client_id.trim(),
      allowed_domains: form.domains.split(',').map(d => d.trim()).filter(Boolean),
      default_role: form.default_role,
      auto_join: form.auto_join,
      enabled: form.enabled,
      require_sso: form.require_sso,
    }
    if (form.client_secret) data.client_secret = form.client_secret
    save.mutate(data)
  }

  const testResult = test.data
  const check = 'h-4 w-4 flex-shrink-0 rounded accent-brand-500'
  return (
    <form onSubmit={e => { e.preventDefault(); submit() }} className="space-y-4" aria-label={t('sso.formLabel', { defaultValue: 'SSO connection' })}>
      <Panel title={t('sso.connectionTitle')}>
        <div className="space-y-3 p-3">
          <p className="text-xs text-gray-400">
            {t('sso.redirectUri', { defaultValue: 'Register this redirect URI at your provider:' })}{' '}
            <code className="break-all rounded bg-surface-950/60 px-1 py-0.5 font-mono text-gray-200">{redirectUri}</code>
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="input-label">{t('sso.preset', { defaultValue: 'Provider' })}</span>
              <select className="select w-full" value={form.preset} onChange={e => set('preset', e.target.value as SsoPreset)}>
                {PRESETS.map(p => <option key={p} value={p}>{t(`sso.presets.${p}`, { defaultValue: p })}</option>)}
              </select>
            </label>
            <label className="block">
              <span className="input-label">{t('sso.slug', { defaultValue: 'SSO name (on the login page)' })}</span>
              <input className="input w-full" required value={form.slug} onChange={e => set('slug', e.target.value)} placeholder={t('sso.slugPlaceholder', { defaultValue: 'acme' })} />
            </label>
            {form.preset === 'generic' && (
              <label className="block sm:col-span-2">
                <span className="input-label">{t('sso.issuer', { defaultValue: 'Issuer URL' })}</span>
                <input className="input w-full" type="url" required value={form.issuer} onChange={e => set('issuer', e.target.value)} placeholder={t('sso.issuerPlaceholder', { defaultValue: 'https://idp.example.com' })} />
              </label>
            )}
            {form.preset === 'entra' && (
              <label className="block sm:col-span-2">
                <span className="input-label">{t('sso.tenant', { defaultValue: 'Directory (tenant) ID' })}</span>
                <input className="input w-full" required value={form.tenant} onChange={e => set('tenant', e.target.value)} />
              </label>
            )}
            <label className="block">
              <span className="input-label">{t('sso.clientId', { defaultValue: 'Client ID' })}</span>
              <input className="input w-full" required value={form.client_id} onChange={e => set('client_id', e.target.value)} />
            </label>
            <label className="block">
              <span className="input-label">{t('sso.clientSecret', { defaultValue: 'Client secret' })}</span>
              <input className="input w-full" type="password" autoComplete="new-password" value={form.client_secret}
                onChange={e => set('client_secret', e.target.value)}
                placeholder={initial?.has_client_secret
                  ? t('sso.secretSet', { defaultValue: 'Set — leave empty to keep it' })
                  : t('sso.secretUnset', { defaultValue: 'Not set' })} />
            </label>
          </div>
        </div>
      </Panel>

      <Panel title={t('sso.accessTitle')}>
        <div className="space-y-3 p-3">
          <div className="grid gap-3 sm:grid-cols-[1fr_12rem]">
            <label className="block">
              <span className="input-label">{t('sso.domains', { defaultValue: 'Allowed email domains (comma-separated)' })}</span>
              <input className="input w-full" required value={form.domains} onChange={e => set('domains', e.target.value)} placeholder={t('sso.domainsPlaceholder', { defaultValue: 'acme.com' })} />
            </label>
            <label className="block">
              <span className="input-label">{t('sso.defaultRole', { defaultValue: 'Role for new members' })}</span>
              <select className="select w-full" value={form.default_role} onChange={e => set('default_role', e.target.value as 'Viewer' | 'Manager')}>
                <option value="Viewer">{t('members.roles.Viewer', { defaultValue: 'Viewer' })}</option>
                <option value="Manager">{t('members.roles.Manager', { defaultValue: 'Manager' })}</option>
              </select>
            </label>
          </div>
          <div className="space-y-2 text-[13px] text-gray-200">
            <label className="flex items-center gap-2">
              <input type="checkbox" className={check} checked={form.enabled} onChange={e => set('enabled', e.target.checked)} />
              {t('sso.enabled', { defaultValue: 'Enabled' })}
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" className={check} checked={form.auto_join} onChange={e => set('auto_join', e.target.checked)} />
              {t('sso.autoJoin', { defaultValue: 'Let people in the allowed domains join on their first sign-in' })}
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" className={check} checked={form.require_sso} onChange={e => set('require_sso', e.target.checked)} />
              {t('sso.requireSso', { defaultValue: 'Require SSO: turn off password sign-in for members' })}
            </label>
            {!ownerSignedIn && (
              <p className="pl-6 text-xs text-gray-400">
                {t('sso.requireHint', { defaultValue: 'Requiring SSO becomes possible once an owner has signed in through this connection.' })}
              </p>
            )}
          </div>
        </div>
      </Panel>

      {save.isError && <div role="alert" className="alert-error text-sm">{t('sso.saveError', { message: errorDetail(save.error), defaultValue: 'Could not save: {{message}}' })}</div>}
      {test.isError && <div role="alert" className="alert-error text-sm">{errorDetail(test.error)}</div>}
      {testResult && (
        <div role="status" className={`${testResult.ok ? 'alert-success' : 'alert-error'} text-sm`}>
          {testResult.ok
            ? <CircleCheck size={15} aria-hidden="true" className="mt-0.5 flex-shrink-0" />
            : <CircleAlert size={15} aria-hidden="true" className="mt-0.5 flex-shrink-0" />}
          <div>
            <p>{testResult.ok
              ? t('sso.testOk', { defaultValue: 'The provider answered: discovery and signing keys fetched.' })
              : t('sso.testFailed', { detail: testResult.detail, defaultValue: 'The provider did not answer as expected: {{detail}}' })}</p>
            {testResult.ok && (
              <p className="mt-1 text-xs text-gray-300">
                {t('sso.testObserved', {
                  keys: String(testResult.observed.key_count ?? 0),
                  issuer: String(testResult.observed.issuer ?? ''),
                  defaultValue: 'Issuer {{issuer}}, {{keys}} signing key(s).',
                })}
              </p>
            )}
          </div>
        </div>
      )}

      <FormActions>
        <button type="submit" className="btn-primary btn-sm" disabled={save.isPending}>
          {save.isPending ? <ButtonSpinner /> : <Save size={13} aria-hidden="true" />}
          {t('sso.save', { defaultValue: 'Save' })}
        </button>
        {initial && (
          <>
            <button type="button" className="btn-tool border border-surface-700" disabled={test.isPending} onClick={() => test.mutate()}>
              {test.isPending ? <ButtonSpinner /> : <FlaskConical size={13} aria-hidden="true" />}
              {t('sso.test', { defaultValue: 'Test connection' })}
            </button>
            <button type="button" className="btn-tool border border-surface-700 hover:text-red-300" disabled={remove.isPending}
              onClick={() => { if (window.confirm(t('sso.deleteConfirm', { defaultValue: 'Remove single sign-on? Password sign-in applies again.' }))) remove.mutate() }}>
              <Trash2 size={13} aria-hidden="true" />
              {t('sso.delete', { defaultValue: 'Remove' })}
            </button>
          </>
        )}
        {save.isSuccess && <SavedNote>{t('sso.saved', { defaultValue: 'Saved.' })}</SavedNote>}
      </FormActions>
    </form>
  )
}
