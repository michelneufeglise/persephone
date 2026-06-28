import { useAppStore } from '@/store/appStore'
import { Input, Textarea } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'

const PERSONALITIES = [
  'Wise & enigmatic',
  'Warm & nurturing',
  'Sharp & direct',
  'Playful & curious',
  'Formal & precise',
  'Philosophical & reflective',
]

const STYLES = [
  { value: 'concise', label: 'Concise — short and direct' },
  { value: 'balanced', label: 'Balanced — natural length' },
  { value: 'elaborate', label: 'Elaborate — detailed and rich' },
]

export function CharacterSection() {
  const { settings, updateCharacter } = useAppStore()
  const char = settings.character

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h3 className="font-serif text-xl text-[var(--text-primary)] mb-1">Character</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Define Persephone's identity, voice, and behavior across all models.
        </p>
      </div>

      <Input
        label="Character Name"
        value={char.name}
        onChange={e => updateCharacter({ name: e.target.value })}
        placeholder="Persephone"
      />

      <Textarea
        label="System Prompt"
        value={char.systemPrompt}
        onChange={e => updateCharacter({ systemPrompt: e.target.value })}
        rows={8}
        placeholder="You are…"
        hint="This sets the fundamental personality and behavior for every conversation."
      />

      <Textarea
        label="User Prompt Prefix"
        value={char.userPromptPrefix}
        onChange={e => updateCharacter({ userPromptPrefix: e.target.value })}
        rows={2}
        placeholder="Optional prefix added before every user message"
        hint="Leave blank to use user messages as-is."
      />

      <div className="grid grid-cols-2 gap-4">
        <Input
          label="Personality Summary"
          value={char.personality}
          onChange={e => updateCharacter({ personality: e.target.value })}
          placeholder="Wise, warm, enigmatic"
          hint="Shown in the UI — does not affect prompts."
        />

        <Select
          label="Response Style"
          value={char.responseStyle}
          onChange={v => updateCharacter({ responseStyle: v as typeof char.responseStyle })}
          options={STYLES}
        />
      </div>

      <Select
        label="Language"
        value={char.language}
        onChange={v => updateCharacter({ language: v })}
        options={[
          { value: 'English', label: 'English' },
          { value: 'French', label: 'Français' },
          { value: 'German', label: 'Deutsch' },
          { value: 'Spanish', label: 'Español' },
          { value: 'Japanese', label: '日本語' },
          { value: 'Chinese', label: '中文' },
          { value: 'Dutch', label: 'Nederlands' },
        ]}
      />

      {/* Personality presets */}
      <div className="space-y-2">
        <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wide">
          Quick Personality
        </label>
        <div className="flex flex-wrap gap-2">
          {PERSONALITIES.map(p => (
            <button
              key={p}
              onClick={() => updateCharacter({ personality: p })}
              className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
                char.personality === p
                  ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                  : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)]'
              }`}
            >
              {p}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
