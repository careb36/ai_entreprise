export function StepIndicator({ current, steps }: { current: number; steps: string[] }) {
  return (
    <div className="flex items-center justify-between mb-8">
      {steps.map((label, i) => (
        <div key={i} className="flex items-center flex-1 last:flex-none">
          <div className="flex flex-col items-center gap-1 flex-shrink-0">
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold transition-all
              ${i < current ? 'bg-brand-600 text-white' : i === current ? 'bg-brand-500 text-white ring-2 ring-brand-400/40' : 'bg-surface-700 text-gray-500'}`}>
              {i < current ? '✓' : i + 1}
            </div>
            <span className={`text-xs whitespace-nowrap
              ${i === current ? 'text-brand-300 font-medium' : i < current ? 'text-gray-400' : 'text-gray-600'}`}>
              {label}
            </span>
          </div>
          {i < steps.length - 1 && (
            <div className={`h-px flex-1 mx-2 mb-5 transition-all ${i < current ? 'bg-brand-600' : 'bg-surface-700'}`} />
          )}
        </div>
      ))}
    </div>
  )
}
