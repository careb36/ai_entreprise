import { useTranslation } from 'react-i18next'
import { PERM_LEVELS, localizedPermission } from './constants'

export function PermRow({ icon, label, desc, permKey, value, options, onChange }: {
  icon: string; label: string; desc: string
  permKey: string; value: string; options: string[]
  onChange: (key: string, val: string) => void
}) {
  const { t } = useTranslation()
  const current = PERM_LEVELS[value as keyof typeof PERM_LEVELS]
  return (
    <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 py-3 border-b border-surface-700 last:border-0">
      <div className="flex items-center gap-3 flex-1 min-w-0">
        <span className="text-xl flex-shrink-0">{icon}</span>
        <div className="min-w-0">
          <p className="text-sm font-medium text-gray-200">{label}</p>
          <p className="text-xs text-gray-500 truncate">{desc}</p>
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <span className={`text-xs font-medium ${current?.color ?? 'text-gray-400'} w-20 text-right`}>
          {localizedPermission(t, value)}
        </span>
        <select value={value} aria-label={label} onChange={e => onChange(permKey, e.target.value)}
          className="select w-36 text-xs">
          {options.map(o => (
            <option key={o} value={o}>{localizedPermission(t, o)}</option>
          ))}
        </select>
      </div>
    </div>
  )
}
