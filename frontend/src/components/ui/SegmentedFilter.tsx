import type { LucideIcon } from 'lucide-react'

export interface SegmentedOption<T extends string> {
  value: T
  label: string
  icon?: LucideIcon | null
  /** Shown after the label in tabular figures; omit for no count. */
  count?: number
  /** Keeps a warning tone while not pressed, so the number that needs a person stands out. */
  attention?: boolean
}

/**
 * Presets that filter a list, side by side, one pressed at a time (the
 * board's "All · Needs a person · High risk · Waiting"). Each is a toggle
 * button with `aria-pressed`, an optional icon, its word and its count.
 * The parent decides what a press means (select, or toggle back to all).
 */
export function SegmentedFilter<T extends string>({ label, options, value, onChange, testIdPrefix }: {
  label: string
  options: SegmentedOption<T>[]
  value: T
  onChange: (value: T) => void
  testIdPrefix?: string
}) {
  return (
    <div role="group" aria-label={label} className="segmented">
      {options.map(({ value: option, label: text, icon: Icon, count, attention }) => {
        const pressed = option === value
        const tone = attention && !pressed ? 'text-red-300 hover:text-red-200' : ''
        return (
          <button key={option} type="button" aria-pressed={pressed} onClick={() => onChange(option)}
            data-testid={testIdPrefix ? `${testIdPrefix}-${option}` : undefined}
            className={`segmented-item ${tone}`}>
            {Icon && <Icon size={12} aria-hidden="true" />}
            {text}
            {count !== undefined && (
              <span className={`tabular-nums ${pressed ? 'text-gray-200' : attention ? 'text-red-300' : 'text-gray-400'}`}>{count}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}
