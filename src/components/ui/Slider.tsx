import { clsx } from 'clsx'

interface SliderProps {
  label?: string
  value: number
  min?: number
  max?: number
  step?: number
  onChange: (v: number) => void
  format?: (v: number) => string
  className?: string
}

export function Slider({
  label,
  value,
  min = 0,
  max = 1,
  step = 0.01,
  onChange,
  format,
  className,
}: SliderProps) {
  const pct = ((value - min) / (max - min)) * 100

  return (
    <div className={clsx('flex flex-col gap-1.5', className)}>
      {label && (
        <div className="flex justify-between items-center">
          <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wide">
            {label}
          </label>
          <span className="text-xs font-mono text-[var(--accent)]">
            {format ? format(value) : value.toFixed(2)}
          </span>
        </div>
      )}
      <div className="relative h-5 flex items-center">
        <div className="absolute inset-x-0 h-1 rounded-full bg-[var(--bg-tertiary)]">
          <div
            className="absolute left-0 top-0 h-full rounded-full bg-[var(--accent)] transition-all duration-100"
            style={{ width: `${pct}%` }}
          />
        </div>
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={e => onChange(Number(e.target.value))}
          className="absolute inset-0 w-full opacity-0 cursor-pointer h-5"
        />
        <div
          className="absolute w-3.5 h-3.5 rounded-full bg-[var(--accent)] border-2 border-[var(--bg-primary)] shadow-md shadow-[var(--accent-glow)] transition-all duration-100"
          style={{ left: `calc(${pct}% - 7px)` }}
        />
      </div>
    </div>
  )
}
