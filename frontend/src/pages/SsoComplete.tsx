import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, CircleAlert, Loader2 } from 'lucide-react'
import { useAuthStore } from '../store/useAuthStore'
import { AuthCard } from './auth/AuthCard'

/** The codes the backend's SSO callback can send back (`routers/sso.py`). */
const KNOWN_ERRORS = new Set([
  'unknown_connection', 'not_configured', 'invalid_state', 'missing_state', 'state_expired',
  'state_mismatch', 'idp_denied', 'idp_error', 'idp_unreachable', 'idp_misconfigured',
  'invalid_token', 'token_expired', 'invalid_nonce', 'email_unverified', 'email_missing',
  'domain_not_allowed', 'not_a_member', 'identity_conflict', 'invalid_handoff',
  'account_exists', 'link_refused', 'account_missing',
])

/**
 * Where the SSO callback lands (`/login/sso`): a one-time `code` is traded
 * for a session, or an `error` code is explained.
 */
export default function SsoComplete() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const loginWithSso = useAuthStore(state => state.loginWithSso)
  const code = searchParams.get('code')
  const [error, setError] = useState<string | null>(searchParams.get('error') ?? (code ? null : 'invalid_handoff'))
  const started = useRef(false)

  useEffect(() => {
    if (!code || started.current) return
    started.current = true // the code is good once: never send it twice
    loginWithSso(code)
      .then(() => navigate('/', { replace: true }))
      .catch(() => setError('invalid_handoff'))
  }, [code, loginWithSso, navigate])

  if (!error) {
    return (
      <AuthCard>
        <div role="status" className="flex items-center gap-3 text-[13px] text-gray-300">
          <Loader2 aria-hidden="true" className="flex-shrink-0 animate-spin text-brand-400 motion-reduce:animate-none" size={20} />
          <p>{t('sso.completing', { defaultValue: 'Signing you in…' })}</p>
        </div>
      </AuthCard>
    )
  }

  const reason = KNOWN_ERRORS.has(error) ? error : 'unknown'
  return (
    <AuthCard title={t('sso.failedTitle', { defaultValue: 'Single sign-on did not complete' })}>
      <p role="alert" className="alert-error mb-5 p-3 text-[13px]">
        <CircleAlert size={16} aria-hidden="true" className="mt-0.5 flex-shrink-0" />
        <span>{t(`sso.errors.${reason}`, { defaultValue: t('sso.errors.unknown', { defaultValue: 'The sign-in could not be completed. Try again.' }) })}</span>
      </p>
      <Link className="btn-secondary w-full" to="/login">
        <ArrowLeft size={15} aria-hidden="true" />
        {t('sso.backToLogin', { defaultValue: 'Back to log in' })}
      </Link>
    </AuthCard>
  )
}
