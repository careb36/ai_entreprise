import { FormEvent, useState } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../services/api'
import { useAuthStore } from '../store/useAuthStore'
import { safeReturnPath } from '../lib/returnPath'
import { CircleAlert, Loader2 } from 'lucide-react'
import { AUTH_LABEL, AUTH_LINK, AuthCard } from './auth/AuthCard'

export default function Register() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const expiresAt = useAuthStore(state => state.expiresAt)
  const [email, setEmail] = useState('')
  const [fullName, setFullName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      // The answer is the same whether or not the email already had an
      // account, so the page never says which (issue #541).
      await api.register({ email, full_name: fullName, password })
      navigate('/login?registered=true', { replace: true })
    } catch (err) {
      setError(err instanceof ApiError && err.status === 429
        ? t('auth.tooManyAttempts', { defaultValue: 'Too many attempts. Please wait a minute and try again.' })
        : err instanceof Error ? err.message : t('auth.registerError', {
          defaultValue: 'Unable to register. Please try again.',
        }))
    } finally {
      setSubmitting(false)
    }
  }

  if (expiresAt && expiresAt > Date.now()) {
    return <Navigate to={safeReturnPath(searchParams.get('from'))} replace />
  }

  return (
    <AuthCard
      title={t('auth.registerTitle', { defaultValue: 'Create an account' })}
      footer={<>
        {t('auth.haveAccount', { defaultValue: 'Already have an account?' })}{' '}
        <Link className={AUTH_LINK} to="/login">
          {t('auth.loginTitle', { defaultValue: 'Log in' })}
        </Link>
      </>}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <span className={AUTH_LABEL}>{t('auth.fullName', { defaultValue: 'Full name' })}</span>
          <input
            className="input"
            type="text"
            autoComplete="name"
            required
            value={fullName}
            onChange={event => setFullName(event.target.value)}
          />
        </label>
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
            autoComplete="new-password"
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
          {t('auth.registerTitle', { defaultValue: 'Create an account' })}
        </button>
      </form>
    </AuthCard>
  )
}
