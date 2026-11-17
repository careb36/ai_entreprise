import { useTranslation } from 'react-i18next'
import type { LucideIcon } from 'lucide-react'
import { PERM_LEVELS, localizedPermission } from './constants'

export function PermRow({ icon: Icon, label, desc, permKey, value, options, onChange }: {
  icon: LucideIcon; label: string; desc: string
  permKey: string; value: string; options: string[]
  onChange: (key: string, val: string) => void
}) {
  const { t } = useTranslation()
  const current = PERM_LEVELS[value as keyof typeof PERM_LEVELS]
  return (
    <li className="data-row flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <Icon size={16} aria-hidden="true" className="flex-shrink-0 text-gray-400" />
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-gray-100">{label}</p>
          <p className="text-xs text-gray-400">{desc}</p>
        </div>
      </div>
      <div className="flex flex-shrink-0 items-center gap-2">
        <span className={`w-20 text-right text-xs font-medium ${current?.color ?? 'text-gray-400'}`}>
          {localizedPermission(t, value)}
        </span>
        <select value={value} aria-label={label} onChange={e => onChange(permKey, e.target.value)}
          className="select control-sm w-36">
          {options.map(o => (
            <option key={o} value={o}>{localizedPermission(t, o)}</option>
          ))}
        </select>
      </div>
    </li>
  )
}
