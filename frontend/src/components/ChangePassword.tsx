import { useId, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { CircleCheck } from 'lucide-react'
import { useChangePassword, usePasswordRule } from '../hooks/usePassword'
import { ApiError, errorDetail } from '../services/api'
import { Panel } from './ui'

const CODES = ['wrong_password', 'too_short', 'too_long', 'same_password', 'locked', 'no_password', 'person_only']

/** The code a refusal of POST /v1/auth/password carries, when it carries one. */
function refusalCode(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null
  const code = (error.data as { code?: unknown } | undefined)?.code
  return typeof code === 'string' && CODES.includes(code) ? code : null
}

/**
 * The signed-in person changes their password (docs/design/change-password.md):
 * the current one, the new one twice. Every other session ends; this browser
 * stays signed in. An SSO-only account has no password and is told so.
 */
export function ChangePassword() {
  const { t } = useTranslation()
  const ids = { current: useId(), next: useId(), repeat: useId(), hint: useId() }
  const rule = usePasswordRule()
  const change = useChangePassword()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [problem, setProblem] = useState<'mismatch' | 'too_short' | null>(null)

  if (rule.isLoading) return null
  if (rule.isError) return <div role="alert" className="alert-error text-sm">{errorDetail(rule.error)}</div>
  const minLength = rule.data?.minLength ?? 12

  if (rule.data && !rule.data.hasPassword) {
    return (
      <Panel title={t('account.password.title')}>
        <p className="p-3 text-sm text-gray-300">{t('account.password.ssoOnly')}</p>
      </Panel>
    )
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    // The backend applies the rule; these only save a round trip.
    if (next.length < minLength) return setProblem('too_short')
    if (next !== repeat) return setProblem('mismatch')
    setProblem(null)
    change.mutate(
      { current, next },
      {
        onSuccess: () => {
          setCurrent('')
          setNext('')
          setRepeat('')
        },
      },
    )
  }

  const code = change.isError ? refusalCode(change.error) : null
  const failure = change.isError
    ? code
      ? t(`account.password.errors.${code}`, { min: minLength })
      : errorDetail(change.error)
    : null
  const edit = (setter: (value: string) => void) => (value: string) => {
    setter(value)
    setProblem(null)
    if (!change.isIdle) change.reset()
  }

  return (
    <Panel title={t('account.password.title')}>
      <form className="space-y-3 p-3" onSubmit={submit}>
        <p id={ids.hint} className="text-xs text-gray-400">{t('account.password.intro', { min: minLength })}</p>
        <label htmlFor={ids.current} className="block text-sm text-gray-300">
          {t('account.password.current')}
          <input id={ids.current} type="password" className="input mt-1 w-full" autoComplete="current-password"
            required value={current} onChange={event => edit(setCurrent)(event.target.value)} />
        </label>
        <label htmlFor={ids.next} className="block text-sm text-gray-300">
          {t('account.password.new')}
          <input id={ids.next} type="password" className="input mt-1 w-full" autoComplete="new-password"
            required minLength={minLength} aria-describedby={ids.hint}
            value={next} onChange={event => edit(setNext)(event.target.value)} />
        </label>
        <label htmlFor={ids.repeat} className="block text-sm text-gray-300">
          {t('account.password.repeat')}
          <input id={ids.repeat} type="password" className="input mt-1 w-full" autoComplete="new-password"
            required minLength={minLength} value={repeat} onChange={event => edit(setRepeat)(event.target.value)} />
        </label>
        {problem && (
          <div role="alert" className="alert-error text-sm">
            {problem === 'mismatch' ? t('account.password.mismatch') : t('account.password.errors.too_short', { min: minLength })}
          </div>
        )}
        {failure && <div role="alert" className="alert-error text-sm">{failure}</div>}
        {change.isSuccess && (
          <p role="status" className="alert-success items-center text-sm">
            <CircleCheck size={15} aria-hidden="true" className="flex-shrink-0" />
            {t('account.password.changed', { count: change.data.sessions_ended })}
          </p>
        )}
        <button type="submit" className="btn-primary btn-sm" disabled={change.isPending}>
          {change.isPending ? t('account.password.saving') : t('account.password.submit')}
        </button>
      </form>
    </Panel>
  )
}
