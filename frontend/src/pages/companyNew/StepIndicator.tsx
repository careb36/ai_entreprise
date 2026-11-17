import { useTranslation } from 'react-i18next'
import { Check } from 'lucide-react'

/**
 * Where the wizard is: each step as its number (a check once done) and its
 * name, the current one marked with `aria-current="step"`. Done, current and
 * to come differ by the mark and the weight, not by colour alone.
 */
export function StepIndicator({ current, steps }: { current: number; steps: string[] }) {
  const { t } = useTranslation()
  return (
    <ol aria-label={t('companyNew.stepsLabel')} className="flex items-center gap-1.5 overflow-x-auto">
      {steps.map((label, i) => {
        const done = i < current
        const now = i === current
        return (
          <li key={i} aria-current={now ? 'step' : undefined} className="flex min-w-0 flex-1 items-center gap-1.5 last:flex-none">
            <span className="flex flex-shrink-0 items-center gap-1.5 whitespace-nowrap">
              <span className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold tabular-nums
                ${done ? 'bg-brand-600 text-white' : now ? 'bg-brand-500 text-white ring-2 ring-brand-400/40' : 'border border-surface-600 text-gray-400'}`}>
                {done ? <Check size={12} aria-hidden="true" /> : i + 1}
              </span>
              <span className={`text-xs ${now ? 'font-semibold text-gray-50' : done ? 'text-gray-300' : 'text-gray-400'}`}>
                {label}
              </span>
              {done && <span className="sr-only">{t('companyNew.stepDone')}</span>}
            </span>
            {i < steps.length - 1 && (
              <span aria-hidden="true" className={`h-px min-w-[0.75rem] flex-1 ${done ? 'bg-brand-600' : 'bg-surface-700'}`} />
            )}
          </li>
        )
      })}
    </ol>
  )
}
