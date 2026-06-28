import { useAppStore } from '@/store/appStore'
import { themes, applyTheme } from '@/themes'
import { Check } from 'lucide-react'

export function ThemeSection() {
  const { settings, updateSettings } = useAppStore()
  const activeTheme = settings.theme

  function selectTheme(id: string) {
    updateSettings({ theme: id })
    applyTheme(id)
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h3 className="font-serif text-xl text-[var(--text-primary)] mb-1">Themes</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Choose the aesthetic that resonates with you.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4">
        {themes.map(theme => (
          <button
            key={theme.id}
            onClick={() => selectTheme(theme.id)}
            className={`relative flex items-center gap-4 p-4 rounded-xl border text-left transition-all duration-300
              ${activeTheme === theme.id
                ? 'border-[var(--accent)] shadow-lg shadow-[var(--accent-glow)] scale-[1.01]'
                : 'border-[var(--border)] hover:border-[var(--border-bright)] hover:scale-[1.005]'
              }`}
            style={{ background: theme.preview.bg }}
          >
            {/* Color preview swatches */}
            <div className="flex-shrink-0 flex gap-1">
              <div className="w-8 h-8 rounded-full" style={{ background: theme.preview.bg, border: '2px solid rgba(255,255,255,0.1)' }} />
              <div className="w-8 h-8 rounded-full" style={{ background: theme.preview.accent }} />
              <div className="w-8 h-8 rounded-full" style={{ background: theme.preview.text, opacity: 0.8 }} />
            </div>

            {/* Info */}
            <div className="flex-1 min-w-0">
              <div className="font-serif text-base" style={{ color: theme.preview.text }}>
                {theme.name}
              </div>
              <div className="text-xs mt-0.5 opacity-60" style={{ color: theme.preview.text }}>
                {theme.description}
              </div>
            </div>

            {/* Selected indicator */}
            {activeTheme === theme.id && (
              <div
                className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center"
                style={{ background: theme.preview.accent }}
              >
                <Check className="w-3.5 h-3.5 text-white" />
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}
