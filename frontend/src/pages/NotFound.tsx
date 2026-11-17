import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Compass } from 'lucide-react'

/** Any route the app does not know: the empty-state look, with the page's one `<h1>`. */
export default function NotFound() {
  const { t } = useTranslation()
  return (
    <div className="page-container flex min-h-[60vh] items-center justify-center">
      <div className="empty-state w-full max-w-md rounded-xl border border-dashed border-surface-700 px-6">
        <Compass size={24} aria-hidden="true" className="empty-state-icon" />
        <p className="text-meta font-mono tabular-nums">404</p>
        <h1 className="page-title">{t('notFound.title')}</h1>
        <p className="empty-state-desc">{t('notFound.description')}</p>
        <Link to="/dashboard" className="btn-primary mt-2">
          {t('notFound.backHome')}
        </Link>
      </div>
    </div>
  )
}
