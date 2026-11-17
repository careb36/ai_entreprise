import { useTranslation } from 'react-i18next'
import { localizedAutonomy } from './constants'

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
      <h2 className="text-lg font-semibold text-white mb-1">{t('companyNew.autonomyLevel')}</h2>
      <p className="text-sm text-gray-400">{t('companyNew.autonomyDescription')}</p>
    </div>

    <div className="space-y-3">
      {autonomyLevels.map(level => (
        <button key={level.value} type="button"
          onClick={() => setAutonomyLevel(level.value)}
          aria-pressed={autonomyLevel === level.value}
          className={`w-full text-left rounded-xl border-2 p-4 transition-all ${autonomyLevel === level.value ? level.color + ' border-opacity-100' : 'border-surface-700 bg-surface-800/30 hover:border-surface-600'}`}>
          <div className="flex items-start gap-3">
            <span className="text-2xl flex-shrink-0 mt-0.5">{level.icon}</span>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-semibold text-white text-sm">{level.label}</span>
                {autonomyLevel === level.value && (
                  <span className={`badge text-xs ${level.badge}`}>{t('companyNew.selected')}</span>
                )}
              </div>
              <p className="text-xs text-gray-400 mt-1">{level.desc}</p>
              <ul className="mt-2 space-y-0.5">
                {level.features.map(f => (
                  <li key={f} className="text-xs text-gray-500 flex items-center gap-1.5">
                    <span className="text-brand-400">·</span> {f}
                  </li>
                ))}
              </ul>
            </div>
            <div className={`w-5 h-5 rounded-full border-2 flex-shrink-0 mt-0.5 flex items-center justify-center ${autonomyLevel === level.value ? 'border-brand-400 bg-brand-600' : 'border-surface-600'}`}>
              {autonomyLevel === level.value && <span className="text-white text-xs">✓</span>}
            </div>
          </div>
        </button>
      ))}
    </div>
  </div>
  )
}
