import { motion } from 'framer-motion'
import { User } from 'lucide-react'

const COLORS = [
  '#8b2252', '#6d28d9', '#2563eb', '#059669',
  '#d97706', '#dc2626', '#db2777', '#0891b2',
]

const DISPLAY_NAMES = ['Persephone', 'Oracle', 'Sage', 'Seeker', 'Shadow', 'Dawn', 'Dusk', 'Keeper']

interface AccountStepProps {
  name: string
  color: string
  onNameChange: (v: string) => void
  onColorChange: (v: string) => void
}

export function AccountStep({ name, color, onNameChange, onColorChange }: AccountStepProps) {
  return (
    <div className="max-w-md mx-auto space-y-8">
      <div className="text-center">
        <h2 className="font-serif text-2xl text-[var(--text-primary)] mb-1">Your Account</h2>
        <p className="text-sm text-[var(--text-muted)]">How should Persephone address you?</p>
      </div>

      {/* Avatar preview */}
      <div className="flex justify-center">
        <motion.div
          animate={{ scale: [1, 1.02, 1] }}
          transition={{ duration: 3, repeat: Infinity }}
          className="w-20 h-20 rounded-full flex items-center justify-center text-2xl font-semibold text-white shadow-xl"
          style={{ background: color, boxShadow: `0 0 30px ${color}60` }}
        >
          {name ? name[0].toUpperCase() : <User className="w-8 h-8" />}
        </motion.div>
      </div>

      {/* Name */}
      <div className="space-y-2">
        <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wide">
          Display Name
        </label>
        <input
          value={name}
          onChange={e => onNameChange(e.target.value)}
          placeholder="Enter your name"
          maxLength={24}
          className="w-full rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)]
            px-4 py-3 text-base text-[var(--text-primary)] font-sans text-center
            placeholder:text-[var(--text-muted)]
            focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]
            transition-colors duration-200"
        />
        {/* Quick names */}
        <div className="flex flex-wrap gap-1.5 justify-center pt-1">
          {DISPLAY_NAMES.map(n => (
            <button
              key={n}
              onClick={() => onNameChange(n)}
              className={`px-2.5 py-1 rounded-full text-xs border transition-all ${
                name === n
                  ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                  : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)]'
              }`}
            >
              {n}
            </button>
          ))}
        </div>
      </div>

      {/* Color */}
      <div className="space-y-2">
        <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wide">
          Avatar Color
        </label>
        <div className="flex gap-3 justify-center flex-wrap">
          {COLORS.map(c => (
            <button
              key={c}
              onClick={() => onColorChange(c)}
              className="w-8 h-8 rounded-full transition-all duration-200 hover:scale-110"
              style={{
                background: c,
                boxShadow: color === c ? `0 0 0 3px var(--bg-primary), 0 0 0 5px ${c}` : 'none',
              }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
