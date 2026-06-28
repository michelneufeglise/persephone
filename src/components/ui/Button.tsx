import { forwardRef } from 'react'
import { clsx } from 'clsx'

type Variant = 'primary' | 'ghost' | 'outline' | 'danger' | 'gold'
type Size = 'sm' | 'md' | 'lg' | 'icon'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'primary', size = 'md', className, children, ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={clsx(
          'relative inline-flex items-center justify-center gap-2 font-sans font-medium transition-all duration-200 select-none rounded-lg disabled:opacity-40 disabled:cursor-not-allowed',
          // sizes
          size === 'sm' && 'px-3 py-1.5 text-xs',
          size === 'md' && 'px-4 py-2 text-sm',
          size === 'lg' && 'px-6 py-3 text-base',
          size === 'icon' && 'p-2 text-sm',
          // variants
          variant === 'primary' && [
            'bg-[var(--accent)] text-white',
            'hover:bg-[var(--accent-hover)] hover:shadow-lg hover:shadow-[var(--accent-glow)]',
            'active:scale-95',
          ],
          variant === 'ghost' && [
            'text-[var(--text-secondary)] bg-transparent',
            'hover:bg-[var(--accent-dim)] hover:text-[var(--text-primary)]',
            'active:scale-95',
          ],
          variant === 'outline' && [
            'border border-[var(--border-bright)] text-[var(--text-primary)] bg-transparent',
            'hover:bg-[var(--accent-dim)] hover:border-[var(--accent)]',
            'active:scale-95',
          ],
          variant === 'danger' && [
            'bg-red-600 text-white',
            'hover:bg-red-500',
            'active:scale-95',
          ],
          variant === 'gold' && [
            'bg-[var(--gold)] text-black font-semibold',
            'hover:opacity-90',
            'active:scale-95',
          ],
          className,
        )}
        {...props}
      >
        {children}
      </button>
    )
  },
)
Button.displayName = 'Button'
