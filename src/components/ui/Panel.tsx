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
        'rounded-xl border border-[var(--border)]',
        glass
          ? 'bg-[var(--bg-glass)] backdrop-blur-md'
          : 'bg-[var(--bg-secondary)]',
        glow && 'shadow-lg shadow-[var(--accent-glow)]',
        className,
      )}
    >
      {children}
    </div>
  )
}
