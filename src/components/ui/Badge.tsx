import { clsx } from 'clsx'

interface BadgeProps {
  children: React.ReactNode
  variant?: 'default' | 'accent' | 'gold' | 'muted'
  className?: string
}

export function Badge({ children, variant = 'default', className }: BadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium',
        variant === 'default' && 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)]',
        variant === 'accent' && 'bg-[var(--accent-dim)] text-[var(--accent)]',
        variant === 'gold' && 'bg-[var(--gold-dim)] text-[var(--gold)]',
        variant === 'muted' && 'bg-transparent text-[var(--text-muted)]',
        className,
      )}
    >
      {children}
    </span>
  )
}
