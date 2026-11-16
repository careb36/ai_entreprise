import { useTranslation } from 'react-i18next'
import { Globe } from 'lucide-react'
import { STORAGE_KEY_LANG } from '../../lib/constants'

/** Toggles the interface between English and Spanish and remembers the choice. */
export function LanguageSwitcher() {
  const { t, i18n } = useTranslation()
  const current = (i18n.resolvedLanguage || i18n.language).split('-')[0] === 'es' ? 'es' : 'en'
  const next = current === 'en' ? 'es' : 'en'

  function toggle() {
    i18n.changeLanguage(next)
    localStorage.setItem(STORAGE_KEY_LANG, next)
  }

  return (
    <button
      onClick={toggle}
      aria-label={t(`language.switchTo.${next}`)}
      className="btn-ghost btn-sm flex items-center gap-1.5 border border-surface-700"
    >
      <Globe size={14} />
      {current.toUpperCase()}
    </button>
  )
}
