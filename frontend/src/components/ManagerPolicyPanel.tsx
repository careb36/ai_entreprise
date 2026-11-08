import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ShieldCheck } from 'lucide-react'
import { errorDetail } from '../services/api'
import {
  MANAGER_RULES,
  useManagerPolicy,
  useSaveManagerPolicy,
  type ManagerPolicy,
  type ManagerPolicyUpdate,
} from '../services/manager'

const LIMITS = ['max_files', 'max_criteria', 'max_files_high_risk'] as const
// The rules that read more facts than the rest: shown apart, still opt-in.
const STRICT = new Set(['approve_specification', 'approve_architecture', 'accept_high_risk'])

function formOf(policy: ManagerPolicy): ManagerPolicyUpdate {
  return {
    rules: [...policy.rules],
    max_files: policy.max_files,
    max_criteria: policy.max_criteria,
    max_files_high_risk: policy.max_files_high_risk,
    publish_enabled: policy.publish_enabled,
  }
}

/**
 * The company's Manager policy: the rules the Manager agent may apply alone,
 * each off until a Manager ticks it, the limits they read and the switch for
 * publishing. A Viewer reads it. Decisions no policy can hand over are listed.
 */
export function ManagerPolicyPanel({ companyId, canManage }: { companyId: number; canManage: boolean }) {
  const { t } = useTranslation()
  const policy = useManagerPolicy(companyId)
  const save = useSaveManagerPolicy(companyId)
  const [form, setForm] = useState<ManagerPolicyUpdate | null>(null)

  useEffect(() => {
    if (policy.data) setForm(formOf(policy.data))
  }, [policy.data])

  if (!policy.data || !form) return null
  const catalogue = policy.data.catalogue
  const rules = MANAGER_RULES.filter(name => name in catalogue)
  const toggle = (name: string) =>
    setForm({
      ...form,
      rules: form.rules.includes(name) ? form.rules.filter(r => r !== name) : [...form.rules, name],
    })

  const ruleRow = (name: string) => (
    <li key={name}>
      <label className="flex items-start gap-2 text-sm text-gray-300">
        <input
          type="checkbox"
          className="mt-1"
          checked={form.rules.includes(name)}
          disabled={!canManage}
          onChange={() => toggle(name)}
        />
        <span>
          <span className="font-medium text-gray-200">{t(`manager.policy.rules.${name}`, { defaultValue: name })}</span>
          <span className="block text-xs text-gray-500">{catalogue[name]}</span>
        </span>
      </label>
    </li>
  )

  return (
    <section aria-labelledby="manager-policy" className="card space-y-4 p-4">
      <div>
        <h2 id="manager-policy" className="flex items-center gap-2 text-base font-semibold text-white">
          <ShieldCheck size={16} aria-hidden="true" />
          {t('manager.policy.title', { defaultValue: 'What the Manager agent may decide alone' })}
        </h2>
        <p className="text-xs text-gray-500">
          {t('manager.policy.hint', { defaultValue: 'Every rule is off until you tick it. Each one decides from what the platform observed, never from the model; you can override any decision it takes.' })}
        </p>
      </div>
      <ul className="space-y-2">{rules.filter(name => !STRICT.has(name)).map(ruleRow)}</ul>
      <div>
        <h3 className="text-sm font-semibold text-gray-200">{t('manager.policy.strictTitle', { defaultValue: 'Strict rules' })}</h3>
        <p className="text-xs text-gray-500">{t('manager.policy.strictHint', { defaultValue: 'Approvals and high-risk acceptances: they read more facts and stay off unless you turn them on.' })}</p>
        <ul className="mt-2 space-y-2">{rules.filter(name => STRICT.has(name)).map(ruleRow)}</ul>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {LIMITS.map(limit => (
          <label key={limit} className="text-xs text-gray-400">
            {t(`manager.policy.limits.${limit}`, { defaultValue: limit })}
            <input
              type="number"
              min={0}
              max={1000}
              className="input mt-1 w-full"
              value={form[limit]}
              disabled={!canManage}
              onChange={event => setForm({ ...form, [limit]: Number(event.target.value) })}
            />
          </label>
        ))}
      </div>
      <label className="flex items-center gap-2 text-sm text-gray-300">
        <input
          type="checkbox"
          checked={form.publish_enabled}
          disabled={!canManage}
          onChange={() => setForm({ ...form, publish_enabled: !form.publish_enabled })}
        />
        {t('manager.policy.publishEnabled', { defaultValue: 'Publishing is on: the publish rule may push branches to the remote' })}
      </label>
      <p className="text-xs text-gray-500">
        {t('manager.policy.alwaysYours', {
          decisions: policy.data.always_escalate.join(', '),
          defaultValue: 'Always yours, whatever the policy says: {{decisions}}',
        })}
      </p>
      {save.isError && (
        <p role="alert" className="text-xs text-red-400">
          {t('manager.policy.saveError', { message: errorDetail(save.error), defaultValue: 'Could not save the policy: {{message}}' })}
        </p>
      )}
      {canManage && (
        <button type="button" className="btn btn-primary btn-sm" disabled={save.isPending} onClick={() => save.mutate(form)}>
          {t('manager.policy.save', { defaultValue: 'Save the policy' })}
        </button>
      )}
    </section>
  )
}
