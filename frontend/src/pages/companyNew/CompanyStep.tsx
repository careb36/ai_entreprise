import { useTranslation } from 'react-i18next'
import { TIMEZONES } from './constants'

/** Step 1: the company's name, description, sprint length and time zone. */
export function CompanyStep({
  companyName, setCompanyName, companyDesc, setCompanyDesc, scopeSize, setScopeSize, timezone, setTimezone,
}: {
  companyName: string
  setCompanyName: (value: string) => void
  companyDesc: string
  setCompanyDesc: (value: string) => void
  scopeSize: 'S' | 'M' | 'L'
  setScopeSize: (value: 'S' | 'M' | 'L') => void
  timezone: string
  setTimezone: (value: string) => void
}) {
  const { t } = useTranslation()
  return (
  <div className="space-y-5">
    <div>
      <h2 className="text-lg font-semibold text-white mb-1">{t('companyNew.companyDetails')}</h2>
      <p className="text-sm text-gray-400">{t('companyNew.companyDescription')}</p>
    </div>

    <div>
      <label className="input-label">{t('companyNew.factoryName')} *</label>
      <input type="text" value={companyName} onChange={e => setCompanyName(e.target.value)}
        placeholder={t('companyNew.companyNamePlaceholder')} required className="input w-full text-base" autoFocus />
      {companyName.length > 0 && companyName.length < 2 && (
        <p className="text-xs text-red-400 mt-1">{t('companyNew.nameTooShort')}</p>
      )}
    </div>

    <div>
      <label className="input-label">{t('companyNew.description')} <span className="text-gray-600 normal-case font-normal">{t('companyNew.optional')}</span></label>
      <textarea value={companyDesc} onChange={e => setCompanyDesc(e.target.value)}
        placeholder={t('companyNew.descriptionPlaceholder')}
        rows={3} className="textarea w-full" />
    </div>

    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div>
        <label className="input-label">{t('companyNew.sprintLength')}</label>
        <select
          value={scopeSize}
          onChange={e => setScopeSize(e.target.value as 'S' | 'M' | 'L')}
          className="select w-full"
        >
          <option value="S">{t('companyNew.scopes.S')}</option>
          <option value="M">{t('companyNew.scopes.M')}</option>
          <option value="L">{t('companyNew.scopes.L')}</option>
        </select>
        <p className="text-xs text-gray-500 mt-1">
          {t(`companyNew.scopeHints.${scopeSize}`)}
        </p>
      </div>
      <div>
        <label className="input-label">{t('companyNew.timezone')}</label>
        <select value={timezone} onChange={e => setTimezone(e.target.value)} className="select w-full">
          {TIMEZONES.map(tz => (
            <option key={tz} value={tz}>{t(`companyNew.timezones.${tz}`, { defaultValue: tz })}</option>
          ))}
        </select>
      </div>
    </div>
  </div>
  )
}
