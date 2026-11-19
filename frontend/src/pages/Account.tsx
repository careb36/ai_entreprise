import { useTranslation } from 'react-i18next'
import { SsoIdentities } from '../components/SsoIdentities'
import { PageHeader } from '../components/ui'

/** The signed-in person's own settings: for now, their single sign-on identities. */
export default function Account() {
  const { t } = useTranslation()
  return (
    <div className="page-container max-w-2xl space-y-4">
      <PageHeader title={t('account.title')} subtitle={t('account.subtitle')} />
      <SsoIdentities />
    </div>
  )
}
