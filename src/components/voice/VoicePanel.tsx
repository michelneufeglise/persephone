import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { VolumeX, Settings2 } from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { VoiceOrb } from './VoiceOrb'
import { Slider } from '@/components/ui/Slider'
import { Toggle } from '@/components/ui/Toggle'
import { stopTTS, enqueueTTS } from '@/lib/tts'

const VOICE_PREVIEW: Record<string, string> = {
  af_heart:    'I dwell between worlds, queen of both.',
  af_bella:    'Every spring is a return.',
  af_nicole:   'Wisdom grows in stillness.',
  af_sarah:    'There is beauty in the quiet places.',
  af_sky:      'Even the dead remember sunlight.',
  af_aoede:    'I carry both worlds inside me.',
  am_adam:     'Six seeds. Six months of shadow.',
  am_michael:  'Light is the reaching, shadow the resting.',
  am_liam:     'Between light and shadow, there lives a queen.',
  am_puck:     'What use is spring without knowing winter?',
  bf_emma:     'I dwell between worlds, queen of both.',
  bf_alice:    'Wisdom grows in stillness, like roots in the dark.',
  bf_isabella: 'There is beauty in the underworld if you know how to look.',
  bm_george:   'Six seeds. Six months of shadow. The price of knowledge.',
  bm_daniel:   'The pomegranate binds me, yet gives me a kingdom.',
  bm_fable:    'Once, a queen lived between two seasons — she was called Persephone.',
}

interface Voice {
  id: string
  name: string
  gender: string
  accent?: string
  description: string
}

export function VoicePanel() {
  const { settings, updateTTSSettings, isSpeaking, isGenerating } = useAppStore()
  const [voices, setVoices]         = useState<Voice[]>([])
  const [showConfig, setShowConfig] = useState(false)
  const [accentFilter, setAccentFilter] = useState<'all' | 'US' | 'UK'>('all')
  const tts = settings.tts

  const filtered = voices.filter(v =>
    accentFilter === 'all' || v.accent === accentFilter,
  )

  useEffect(() => {
    fetch('/api/tts/voices').then(r => r.json()).then(d => setVoices(d.voices ?? [])).catch(() => {})
  }, [])

  const statusText = isSpeaking
    ? 'Speaking…'
    : isGenerating
    ? 'Thinking…'
    : tts.enabled
    ? 'Ready'
    : 'Voice off'

  function previewVoice(v: Voice) {
    updateTTSSettings({ voice: v.id })
    stopTTS()
    enqueueTTS(VOICE_PREVIEW[v.id] ?? `Hello, I am ${v.name}.`, v.id, tts.speed, tts.volume)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Orb area */}
      <div className="flex flex-col items-center justify-center py-6 gap-3 border-b border-[var(--border)]">
        <VoiceOrb />
        <motion.div
          animate={{ opacity: [0.6, 1, 0.6] }}
          transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
          className="text-[10px] font-mono text-[var(--text-secondary)] tracking-widest uppercase"
        >
          {statusText}
        </motion.div>
        <div className="flex items-center gap-3">
          <Toggle
            checked={tts.enabled}
            onChange={v => { updateTTSSettings({ enabled: v }); if (!v) stopTTS() }}
            label="Voice on"
          />
          {isSpeaking && (
            <button onClick={() => stopTTS()}
              className="flex items-center gap-1 text-xs text-[var(--accent)] hover:text-[var(--accent-hover)] transition-colors">
              <VolumeX className="w-3.5 h-3.5" />Stop
            </button>
          )}
        </div>
      </div>

      {/* Config toggle */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-[var(--border)]">
        <span className="text-[10px] text-[var(--text-muted)] uppercase tracking-wide">
          {showConfig ? 'Configuration' : 'Voice'}
        </span>
        <button
          onClick={() => setShowConfig(v => !v)}
          className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors"
        >
          <Settings2 className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto" style={{ scrollbarWidth: 'thin' }}>
        <AnimatePresence mode="wait">
          {showConfig ? (
            <motion.div
              key="config"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="px-4 py-3 space-y-5"
            >
              <div className="space-y-2">
                <label className="text-xs font-medium text-[var(--text-secondary)] uppercase tracking-wide">
                  Voice Character
                </label>
                {/* Accent filter */}
                <div className="flex gap-1.5">
                  {(['all', 'US', 'UK'] as const).map(f => (
                    <button
                      key={f}
                      onClick={() => setAccentFilter(f)}
                      className={`px-2.5 py-1 rounded-full text-[10px] font-mono uppercase tracking-wider border transition-all ${
                        accentFilter === f
                          ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                          : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)]'
                      }`}
                    >
                      {f === 'all' ? 'all' : f === 'US' ? '🇺🇸 us' : '🇬🇧 uk'}
                    </button>
                  ))}
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {filtered.map(v => (
                    <button
                      key={v.id}
                      onClick={() => previewVoice(v)}
                      className={`p-2.5 rounded-lg text-left border transition-all duration-200 ${
                        tts.voice === v.id
                          ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-sm shadow-[var(--accent-glow)]'
                          : 'border-[var(--border)] bg-[var(--bg-tertiary)] hover:border-[var(--border-bright)]'
                      }`}
                    >
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-medium text-[var(--text-primary)]">{v.name}</span>
                        {v.accent && (
                          <span className="text-[8px] font-mono uppercase tracking-wider px-1 py-px rounded border border-[var(--border)] text-[var(--text-muted)]">
                            {v.accent}
                          </span>
                        )}
                      </div>
                      <div className="text-[10px] text-[var(--text-muted)] mt-0.5">{v.description}</div>
                    </button>
                  ))}
                </div>
              </div>

              <Slider label="Speed" value={tts.speed} min={0.5} max={2.0} step={0.05}
                onChange={v => updateTTSSettings({ speed: v })} format={v => `${v.toFixed(2)}×`} />
              <Slider label="Volume" value={tts.volume} min={0} max={1} step={0.05}
                onChange={v => updateTTSSettings({ volume: v })} format={v => `${Math.round(v * 100)}%`} />
              <Toggle checked={tts.autoPlay} onChange={v => updateTTSSettings({ autoPlay: v })}
                label="Auto-play responses" description="Speak AI responses automatically" />
            </motion.div>
          ) : (
            <motion.div
              key="grid"
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="px-4 py-3"
            >
              <div className="grid grid-cols-2 gap-1.5">
                {voices.map(v => (
                  <button
                    key={v.id}
                    onClick={() => previewVoice(v)}
                    className={`px-2 py-1.5 rounded-lg text-xs font-medium border transition-all inline-flex items-center justify-center gap-1.5 ${
                      tts.voice === v.id
                        ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                        : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)]'
                    }`}
                  >
                    <span>{v.name}</span>
                    {v.accent && (
                      <span className="text-[8px] opacity-60">{v.accent}</span>
                    )}
                  </button>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
