import { FormEvent, useState } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ApiError, ssoStartUrl } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'
import { safeReturnPath } from '../lib/returnPath'
import { CircleAlert, CircleCheck, KeyRound, Loader2 } from 'lucide-react'
import { AUTH_LABEL, AUTH_LINK, AuthCard } from './auth/AuthCard'

export default function Login() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const login = useAuthStore(state => state.login)
  const expiresAt = useAuthStore(state => state.expiresAt)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [ssoKey, setSsoKey] = useState('')
  const from = searchParams.get('from')

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      await login(email, password)
      navigate(safeReturnPath(from), { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) {
        setError(t('auth.notConfigured', {
          defaultValue: 'Authentication is not configured on this server.',
        }))
      } else if (err instanceof ApiError && err.status === 403) {
        setError(t('sso.requiredForCompany', {
          defaultValue: 'Your company requires single sign-on. Use Sign in with SSO below.',
        }))
      } else if (err instanceof ApiError && err.status === 401) {
        setError(t('auth.invalidCredentials', {
          defaultValue: 'Invalid email or password.',
        }))
      } else if (err instanceof ApiError && err.status === 429) {
        // Limited per address and per account (issue #541).
        setError(t('auth.tooManyAttempts', {
          defaultValue: 'Too many attempts. Please wait a minute and try again.',
        }))
      } else {
        setError(err instanceof Error ? err.message : t('auth.loginError', {
          defaultValue: 'Unable to log in. Please try again.',
        }))
      }
    } finally {
      setSubmitting(false)
    }
  }

  function startSso(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!ssoKey.trim()) return
    // A full-page navigation: the backend sets a cookie and redirects to the provider.
    window.location.assign(ssoStartUrl(ssoKey))
  }

  if (expiresAt && expiresAt > Date.now() && !submitting) {
    return <Navigate to={safeReturnPath(from)} replace />
  }

  return (
    <AuthCard
      title={t('auth.loginTitle', { defaultValue: 'Log in' })}
      footer={<>
        {t('auth.noAccount', { defaultValue: "Don't have an account?" })}{' '}
        <Link className={AUTH_LINK} to="/register">
          {t('auth.registerLink', { defaultValue: 'Register' })}
        </Link>
      </>}
    >
      {searchParams.get('registered') === 'true' && (
        <p role="status" className="alert-success mb-4 p-3 text-[13px]">
          <CircleCheck size={16} aria-hidden="true" className="mt-0.5 flex-shrink-0" />
          <span>{t('auth.registerSuccess', { defaultValue: 'If this email was not registered already, your account is ready. Log in to continue.' })}</span>
        </p>
      )}
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <span className={AUTH_LABEL}>{t('auth.email', { defaultValue: 'Email' })}</span>
          <input
            className="input"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={event => setEmail(event.target.value)}
          />
        </label>
        <label className="block">
          <span className={AUTH_LABEL}>{t('auth.password', { defaultValue: 'Password' })}</span>
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={event => setPassword(event.target.value)}
          />
        </label>
        {error && (
          <p role="alert" className="alert-error p-3 text-[13px]">
            <CircleAlert size={16} aria-hidden="true" className="mt-0.5 flex-shrink-0" />
            <span>{error}</span>
          </p>
        )}
        <button className="btn-primary w-full" type="submit" disabled={submitting}>
          {submitting && <Loader2 size={15} aria-hidden="true" className="animate-spin motion-reduce:animate-none" />}
          {submitting
            ? t('auth.loggingIn', { defaultValue: 'Logging in…' })
            : t('auth.loginTitle', { defaultValue: 'Log in' })}
        </button>
      </form>
      <form onSubmit={startSso} className="mt-5 space-y-3 border-t border-surface-700 pt-5" aria-labelledby="sso-title">
        <h2 id="sso-title" className="panel-title">
          {t('sso.loginTitle', { defaultValue: 'Single sign-on' })}
        </h2>
        <label className="block">
          <span className={AUTH_LABEL}>{t('sso.loginKey', { defaultValue: 'Work email or company SSO name' })}</span>
          <input
            className="input"
            type="text"
            autoComplete="username"
            value={ssoKey}
            onChange={event => setSsoKey(event.target.value)}
            placeholder={t('sso.loginKeyPlaceholder', { defaultValue: 'name@company.com or acme' })}
          />
        </label>
        <button className="btn-secondary w-full" type="submit" disabled={!ssoKey.trim()}>
          <KeyRound size={15} aria-hidden="true" />
          {t('sso.signIn', { defaultValue: 'Sign in with SSO' })}
        </button>
      </form>
    </AuthCard>
  )
}
