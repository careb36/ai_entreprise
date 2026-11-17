import { useTranslation } from 'react-i18next'
import { Check } from 'lucide-react'
import { StatusBadge } from '../../components/ui'
import { localizedAutonomy } from './constants'
import { AUTONOMY_ICONS } from './icons'

/** Step 2: how much the agents may do without a person. */
export function AutonomyStep({ autonomyLevel, setAutonomyLevel }: {
  autonomyLevel: number
  setAutonomyLevel: (value: number) => void
}) {
  const { t } = useTranslation()
  const autonomyLevels = localizedAutonomy(t)
  return (
  <div className="space-y-4">
    <div>
      <h2 className="mb-1 text-base font-semibold text-gray-50">{t('companyNew.autonomyLevel')}</h2>
      <p className="text-[13px] text-gray-400">{t('companyNew.autonomyDescription')}</p>
    </div>

    <div className="space-y-2">
      {autonomyLevels.map(level => {
        const selected = autonomyLevel === level.value
        const Icon = AUTONOMY_ICONS[level.value]
        return (
          <button key={level.value} type="button"
            onClick={() => setAutonomyLevel(level.value)}
            aria-pressed={selected}
            className={`w-full rounded-lg border p-3 text-left transition-colors motion-reduce:transition-none
              ${selected ? 'border-brand-500 bg-brand-950/30 ring-1 ring-inset ring-brand-500/40' : 'border-surface-700 bg-surface-950/30 hover:border-surface-600 hover:bg-surface-800/40'}`}>
            <div className="flex items-start gap-3">
              <span className={`mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md border
                ${selected ? 'border-brand-600/60 text-brand-200' : 'border-surface-700 text-gray-300'}`}>
                {Icon && <Icon size={16} aria-hidden="true" />}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[13px] font-semibold text-gray-50">{level.label}</span>
                  {selected && <StatusBadge icon={Check} tone="brand" label={t('companyNew.selected')} />}
                </div>
                <p className="mt-1 text-xs text-gray-300">{level.desc}</p>
                <ul className="mt-2 space-y-0.5">
                  {level.features.map(f => (
                    <li key={f} className="flex items-center gap-1.5 text-xs text-gray-400">
                      <span aria-hidden="true" className="text-brand-300">·</span> {f}
                    </li>
                  ))}
                </ul>
              </div>
              <span aria-hidden="true" className={`mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full border
                ${selected ? 'border-brand-400 bg-brand-600 text-white' : 'border-surface-600'}`}>
                {selected && <Check size={10} />}
              </span>
            </div>
          </button>
        )
      })}
    </div>
  </div>
  )
}
