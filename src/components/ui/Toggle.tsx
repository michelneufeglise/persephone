import { clsx } from 'clsx'
import { motion } from 'framer-motion'

interface ToggleProps {
  checked: boolean
  onChange: (v: boolean) => void
  label?: string
  description?: string
  disabled?: boolean
}

export function Toggle({ checked, onChange, label, description, disabled }: ToggleProps) {
  return (
    <div className="flex items-start gap-3">
      <button
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={clsx(
          'relative inline-flex h-6 w-11 flex-shrink-0 rounded-full border-2 border-transparent',
          'transition-colors duration-200 focus:outline-none cursor-pointer',
          checked ? 'bg-[var(--accent)]' : 'bg-[var(--bg-tertiary)]',
          disabled && 'opacity-40 cursor-not-allowed',
        )}
      >
        <motion.span
          className="pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow-sm"
          animate={{ x: checked ? 20 : 2 }}
          transition={{ type: 'spring', stiffness: 500, damping: 30 }}
        />
      </button>
      {(label || description) && (
        <div className="flex flex-col gap-0.5 min-w-0">
          {label && (
            <span className="text-sm font-medium text-[var(--text-primary)]">{label}</span>
          )}
          {description && (
            <span className="text-xs text-[var(--text-muted)] leading-relaxed">{description}</span>
          )}
        </div>
      )}
    </div>
  )
}
