import { clsx } from 'clsx'

interface PanelProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode
  className?: string
  glass?: boolean
  glow?: boolean
}

export function Panel({ children, className, glass, glow, ...props }: PanelProps) {
  return (
    <div
      {...props}
      className={clsx(
        'rounded-xl border border-[var(--glass-stroke)]',
        glass
          ? 'bg-[var(--glass-tint)] backdrop-blur-md -webkit-backdrop-filter-[blur(var(--glass-blur)_saturate(var(--glass-saturate)))]'
          : 'bg-[var(--glass-fill)]',
        glow && 'shadow-lg shadow-[var(--accent-glow)]',
        className,
      )}
    >
      {children}
    </div>
  )
}
