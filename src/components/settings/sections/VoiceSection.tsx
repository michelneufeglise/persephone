import { useState, useEffect } from 'react'
import { useAppStore } from '@/store/appStore'
import { Slider } from '@/components/ui/Slider'
import { Toggle } from '@/components/ui/Toggle'
import { Panel } from '@/components/ui/Panel'
import { speakText } from '@/lib/tts'
import { Button } from '@/components/ui/Button'
import { Play } from 'lucide-react'

interface Voice {
  id: string
  name: string
  gender: string
  accent?: string
  description: string
}

export function VoiceSection() {
  const { settings, updateTTSSettings } = useAppStore()
  const tts = settings.tts
  const [voices, setVoices]      = useState<Voice[]>([])
  const [testing, setTesting]    = useState(false)
  const [accentFilter, setAccentFilter] = useState<'all' | 'US' | 'UK' | 'ES'>('all')

  const filtered = voices.filter(v =>
    accentFilter === 'all' || v.accent === accentFilter,
  )

  useEffect(() => {
    fetch('/api/tts/voices')
      .then(r => r.json())
      .then(d => setVoices(d.voices ?? []))
      .catch(() => {})
  }, [])

  async function handleTest() {
    if (testing) return
    setTesting(true)
    try {
      await speakText(
        `Hello. I am ${settings.character.name}. My voice is ${tts.voice}, and I speak at ${tts.speed.toFixed(1)} times normal speed.`,
        tts.voice,
        tts.speed,
        tts.volume,
        undefined,
        () => setTesting(false),
      )
    } catch {
      setTesting(false)
    }
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h3 className="font-serif text-xl text-[var(--text-primary)] mb-1">Voice & TTS</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Powered by Kokoro-82M — a locally-running neural TTS at 24kHz, ~10× real-time.
        </p>
      </div>

      <Panel className="p-4 space-y-4">
        <Toggle
          checked={tts.enabled}
          onChange={v => updateTTSSettings({ enabled: v })}
          label="Enable Voice"
          description="Generate speech for AI responses using Kokoro TTS"
        />
        <Toggle
          checked={tts.autoPlay}
          onChange={v => updateTTSSettings({ autoPlay: v })}
          label="Auto-play responses"
          description="Automatically speak each AI response"
        />
      </Panel>

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wide">
            Voice Character
          </label>
          <div className="flex gap-1.5">
            {(['all', 'US', 'UK', 'ES'] as const).map(f => (
              <button
                key={f}
                onClick={() => setAccentFilter(f)}
                className={`px-2.5 py-1 rounded-full text-[10px] font-mono uppercase tracking-wider border transition-all ${
                  accentFilter === f
                    ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                    : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)]'
                }`}
              >
                {f === 'all'
                  ? 'all'
                  : f === 'US' ? '🇺🇸 us'
                  : f === 'UK' ? '🇬🇧 uk'
                  : '🇪🇸 es'}
              </button>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {filtered.map(v => (
            <button
              key={v.id}
              onClick={() => updateTTSSettings({ voice: v.id })}
              className={`p-3 rounded-xl text-left border transition-all duration-200 ${
                tts.voice === v.id
                  ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-md shadow-[var(--accent-glow)]'
                  : 'border-[var(--border)] bg-[var(--bg-secondary)] hover:border-[var(--border-bright)]'
              }`}
            >
              <div className="flex items-center justify-between mb-1 flex-wrap gap-1">
                <div className="flex items-center gap-1.5">
                  <span className="text-sm font-medium text-[var(--text-primary)]">{v.name}</span>
                  {v.accent && (
                    <span className="text-[9px] font-mono uppercase tracking-wider px-1 py-px rounded border border-[var(--border)] text-[var(--text-muted)]">
                      {v.accent}
                    </span>
                  )}
                </div>
                <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                  v.gender === 'female'
                    ? 'bg-pink-500/20 text-pink-300'
                    : 'bg-blue-500/20 text-blue-300'
                }`}>
                  {v.gender}
                </span>
              </div>
              <div className="text-xs text-[var(--text-muted)]">{v.description}</div>
            </button>
          ))}
        </div>
      </div>

      <Panel className="p-4 space-y-5">
        <Slider
          label="Speed"
          value={tts.speed}
          min={0.5} max={2.0} step={0.05}
          onChange={v => updateTTSSettings({ speed: v })}
          format={v => `${v.toFixed(2)}×`}
        />
        <Slider
          label="Volume"
          value={tts.volume}
          min={0} max={1} step={0.05}
          onChange={v => updateTTSSettings({ volume: v })}
          format={v => `${Math.round(v * 100)}%`}
        />
      </Panel>

      <Button
        variant="outline"
        onClick={handleTest}
        disabled={testing || !tts.enabled}
        className="flex items-center gap-2"
      >
        <Play className="w-4 h-4" />
        {testing ? 'Generating…' : 'Test Voice'}
      </Button>
    </div>
  )
}
