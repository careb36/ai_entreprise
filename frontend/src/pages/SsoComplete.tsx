import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import { useAuthStore } from '../store/useAuthStore'

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
      <div className="page-container max-w-md" role="status">
        <Loader2 className="animate-spin text-brand-400 mb-3" size={22} />
        <p className="text-gray-300">{t('sso.completing', { defaultValue: 'Signing you in…' })}</p>
      </div>
    )
  }

  const reason = KNOWN_ERRORS.has(error) ? error : 'unknown'
  return (
    <div className="page-container max-w-md">
      <h1 className="text-2xl font-bold text-white mb-4">
        {t('sso.failedTitle', { defaultValue: 'Single sign-on did not complete' })}
      </h1>
      <p role="alert" className="text-red-300 mb-4">
        {t(`sso.errors.${reason}`, { defaultValue: t('sso.errors.unknown', { defaultValue: 'The sign-in could not be completed. Try again.' }) })}
      </p>
      <Link className="text-brand-300" to="/login">
        {t('sso.backToLogin', { defaultValue: 'Back to log in' })}
      </Link>
    </div>
  )
}
