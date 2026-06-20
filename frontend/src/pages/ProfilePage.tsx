import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import {
  User, Mail, Shield, Save, Loader2, CheckCircle2,
  AlertCircle, LogOut, Hash,
} from 'lucide-react'
import { useAuthStore } from '../store/authStore'
import { useMe, useUpdateMe } from '../hooks/useQueries'
import { getInitials } from '../lib/avatar'

// ── Role badge colours ────────────────────────────────────────────────────────

const ROLE_STYLE: Record<string, string> = {
  Owner:   'bg-purple-900/40 text-purple-300 border-purple-700/40',
  Manager: 'bg-brand-900/40  text-brand-300  border-brand-700/40',
  Viewer:  'bg-gray-800      text-gray-400   border-surface-600',
}

// ── Avatar with initials ──────────────────────────────────────────────────────

function Avatar({ name, email }: { name?: string; email: string }) {
  const text = getInitials(name, email)
  return (
    <div className="w-20 h-20 rounded-2xl bg-brand-600/20 border-2 border-brand-600/40 flex items-center justify-center">
      <span className="text-3xl font-black text-brand-300">{text}</span>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function ProfilePage() {
  const { t } = useTranslation()
  const user        = useAuthStore((s) => s.user)
  const clearAuth   = useAuthStore((s) => s.clearAuth)

  const { data: me, isLoading: meLoading } = useMe()
  const updateMe = useUpdateMe()

  const [fullName, setFullName] = useState('')
  const [saved, setSaved]       = useState(false)
  const [fieldError, setFieldError] = useState<string | null>(null)
  const savedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Seed the input from the live /me response (preferred) or the store
  useEffect(() => {
    if (me?.full_name !== undefined) {
      setFullName(me.full_name)
    } else if (user?.full_name) {
      setFullName(user.full_name)
    }
  }, [me, user])

  // Clear the "saved" timer on unmount to avoid state updates on an unmounted component
  useEffect(() => {
    return () => {
      if (savedTimerRef.current) clearTimeout(savedTimerRef.current)
    }
  }, [])

  const roleName  = me?.role ?? user?.role ?? 'Viewer'
  const roleStyle = ROLE_STYLE[roleName] ?? ROLE_STYLE['Viewer']
  const email     = me?.email ?? user?.email ?? ''

  function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setFieldError(null)
    setSaved(false)

    const trimmed = fullName.trim()
    if (trimmed.length > 80) {
      setFieldError(t('profile.fullNameTooLong'))
      return
    }

    updateMe.mutate(
      { full_name: trimmed },
      {
        onSuccess: () => {
          setSaved(true)
          if (savedTimerRef.current) clearTimeout(savedTimerRef.current)
          savedTimerRef.current = setTimeout(() => setSaved(false), 3000)
        },
        onError: () => {
          setFieldError(t('profile.saveError'))
        },
      },
    )
  }

  function handleSignOut() {
    clearAuth()
    window.location.replace('/login')
  }

  return (
    <div className="page-container max-w-2xl">
      <div className="mb-8">
        <h1 className="page-title">{t('profile.title')}</h1>
        <p className="page-subtitle">{t('profile.subtitle')}</p>
      </div>

      {/* ── Avatar + meta ──────────────────────────────────────────────────── */}
      <div className="card p-6 mb-4">
        <div className="flex items-center gap-5">
          {meLoading ? (
            <div className="w-20 h-20 rounded-2xl bg-surface-700 animate-pulse" />
          ) : (
            <Avatar name={me?.full_name || user?.full_name} email={email} />
          )}
          <div>
            <p className="text-lg font-bold text-white leading-tight">
              {me?.full_name || user?.full_name || email}
            </p>
            <p className="text-sm text-gray-400 mt-0.5">{email}</p>
            <span className={`inline-flex items-center gap-1 mt-2 px-2 py-0.5 rounded-full border text-xs font-medium ${roleStyle}`}>
              <Shield size={11} />
              {roleName}
            </span>
          </div>
        </div>
      </div>

      {/* ── Personal info form ─────────────────────────────────────────────── */}
      <form onSubmit={handleSave} className="card p-6 mb-4 space-y-5">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <User size={15} className="text-brand-400" />
          {t('profile.personalInfo')}
        </h2>

        {/* Success banner */}
        {saved && (
          <div className="flex items-center gap-2 rounded-lg bg-green-950/40 border border-green-700/40 px-3 py-2 text-sm text-green-300">
            <CheckCircle2 size={15} className="flex-shrink-0" />
            {t('profile.saveSuccess')}
          </div>
        )}

        {/* Error banner */}
        {(fieldError || updateMe.isError) && (
          <div className="flex items-center gap-2 rounded-lg bg-red-950/40 border border-red-700/40 px-3 py-2 text-sm text-red-300">
            <AlertCircle size={15} className="flex-shrink-0" />
            {fieldError ?? t('profile.saveError')}
          </div>
        )}

        {/* Display name */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-gray-400 uppercase tracking-wider">
            {t('profile.fullNameLabel')}
          </label>
          <input
            type="text"
            value={fullName}
            onChange={(e) => { setFullName(e.target.value); setFieldError(null) }}
            className="input w-full"
            placeholder={t('profile.fullNamePlaceholder')}
            maxLength={80}
          />
        </div>

        {/* Email (read-only) */}
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-gray-400 uppercase tracking-wider flex items-center gap-1.5">
            <Mail size={12} />
            {t('profile.emailLabel')}
          </label>
          <input
            type="email"
            value={email}
            disabled
            className="input w-full opacity-50 cursor-not-allowed"
          />
          <p className="text-[11px] text-gray-600">{t('profile.emailHint')}</p>
        </div>

        <div className="flex justify-end">
          <button
            type="submit"
            disabled={updateMe.isPending}
            className="btn btn-primary flex items-center gap-2"
          >
            {updateMe.isPending ? (
              <Loader2 size={15} className="animate-spin" />
            ) : (
              <Save size={15} />
            )}
            {updateMe.isPending ? t('profile.saving') : t('profile.save')}
          </button>
        </div>
      </form>

      {/* ── Account info ──────────────────────────────────────────────────── */}
      <div className="card p-6 mb-4">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2 mb-4">
          <Hash size={15} className="text-brand-400" />
          {t('profile.accountInfo')}
        </h2>
        <dl className="space-y-3">
          <div className="flex justify-between text-sm">
            <dt className="text-gray-400">{t('profile.userId')}</dt>
            <dd className="text-gray-200 font-mono">#{me?.user_id ?? user?.user_id}</dd>
          </div>
          <div className="flex justify-between text-sm">
            <dt className="text-gray-400">{t('profile.roleLabel')}</dt>
            <dd>
              <span className={`px-2 py-0.5 rounded-full border text-xs font-medium ${roleStyle}`}>
                {roleName}
              </span>
            </dd>
          </div>
        </dl>
      </div>

      {/* ── Sign out ──────────────────────────────────────────────────────── */}
      <div className="card p-6 border-red-900/30">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2 mb-1">
          <LogOut size={15} className="text-red-400" />
          {t('profile.dangerZone')}
        </h2>
        <p className="text-xs text-gray-500 mb-4">{t('profile.signOutDesc')}</p>
        <button
          type="button"
          onClick={handleSignOut}
          className="btn btn-danger btn-sm flex items-center gap-2"
        >
          <LogOut size={14} />
          {t('profile.signOut')}
        </button>
      </div>
    </div>
  )
}
