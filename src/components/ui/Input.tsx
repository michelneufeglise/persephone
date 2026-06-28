import { forwardRef } from 'react'
import { clsx } from 'clsx'

interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string
  hint?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, hint, className, ...props }, ref) => (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wide">
          {label}
        </label>
      )}
      <input
        ref={ref}
        className={clsx(
          'w-full rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)]',
          'px-3 py-2 text-sm text-[var(--text-primary)] font-sans',
          'placeholder:text-[var(--text-muted)]',
          'focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]',
          'transition-colors duration-200',
          className,
        )}
        {...props}
      />
      {hint && <p className="text-xs text-[var(--text-muted)]">{hint}</p>}
    </div>
  ),
)
Input.displayName = 'Input'

interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string
  hint?: string
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ label, hint, className, ...props }, ref) => (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wide">
          {label}
        </label>
      )}
      <textarea
        ref={ref}
        className={clsx(
          'w-full rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)]',
          'px-3 py-2 text-sm text-[var(--text-primary)] font-sans leading-relaxed resize-none',
          'placeholder:text-[var(--text-muted)]',
          'focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]',
          'transition-colors duration-200',
          className,
        )}
        {...props}
      />
      {hint && <p className="text-xs text-[var(--text-muted)]">{hint}</p>}
    </div>
  ),
)
Textarea.displayName = 'Textarea'
