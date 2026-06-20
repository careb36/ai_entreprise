import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Loader2, UserPlus, Eye, EyeOff, AlertCircle, CheckCircle2 } from 'lucide-react'
import { api } from '../services/api'

export default function RegisterPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      await api.register({ email, password, full_name: fullName.trim() || undefined })
      setSuccess(true)
      setTimeout(() => navigate('/login'), 2000)
    } catch (err: any) {
      setError(err?.message ?? t('auth.registerError'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-surface-950 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <img src="/fabrique-icon.svg" alt="La Fabrique" width={48} height={48} className="rounded-xl mb-3" />
          <h1 className="text-2xl font-black tracking-tight text-white">La Fabrique</h1>
          <p className="text-sm text-gray-400 mt-1">{t('auth.registerSubtitle')}</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="card p-6 space-y-4">
          {error && (
            <div className="flex items-center gap-2 rounded-lg bg-red-950/40 border border-red-700/40 px-3 py-2 text-sm text-red-300">
              <AlertCircle size={15} className="flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}
          {success && (
            <div className="flex items-center gap-2 rounded-lg bg-green-950/40 border border-green-700/40 px-3 py-2 text-sm text-green-300">
              <CheckCircle2 size={15} className="flex-shrink-0" />
              <span>{t('auth.registerSuccess')}</span>
            </div>
          )}

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
              {t('auth.fullName')} <span className="text-gray-600 normal-case">({t('common.optional')})</span>
            </label>
            <input
              type="text"
              autoComplete="name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className="input w-full"
              placeholder="Jane Doe"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
              {t('auth.email')}
            </label>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="input w-full"
              placeholder="you@example.com"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
              {t('auth.password')}
            </label>
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                required
                minLength={8}
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="input w-full pr-10"
                placeholder="••••••••"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-gray-500 hover:text-gray-300"
                aria-label={showPassword ? t('auth.hidePassword') : t('auth.showPassword')}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
            <p className="text-[11px] text-gray-600">{t('auth.passwordHint')}</p>
          </div>

          <button
            type="submit"
            disabled={loading || success}
            className="btn btn-primary w-full flex items-center justify-center gap-2"
          >
            {loading ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <UserPlus size={16} />
            )}
            {loading ? t('auth.registering') : t('auth.register')}
          </button>
        </form>

        <p className="text-center text-sm text-gray-500 mt-4">
          {t('auth.haveAccount')}{' '}
          <Link to="/login" className="text-brand-400 hover:text-brand-300 font-medium">
            {t('auth.login')}
          </Link>
        </p>
      </div>
    </div>
  )
}
