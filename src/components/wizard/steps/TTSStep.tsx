import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Play, Square, Volume2, CheckCircle } from 'lucide-react'
import { Slider } from '@/components/ui/Slider'
import { speakText, stopTTS } from '@/lib/tts'

const VOICES = [
  // American female
  { id: 'af_heart',    name: 'Heart',    gender: 'female', accent: 'US', description: 'Warm, inviting — richest expressive range',   sample: 'I dwell between worlds — between light and shadow, spring and winter.' },
  { id: 'af_bella',    name: 'Bella',    gender: 'female', accent: 'US', description: 'Bright, playful, youthful',                    sample: 'Every spring is a return. Every autumn, a descent.' },
  { id: 'af_nicole',   name: 'Nicole',   gender: 'female', accent: 'US', description: 'Clear, articulate, professional',              sample: 'Wisdom grows in stillness, like roots drinking from the dark.' },
  { id: 'af_sarah',    name: 'Sarah',    gender: 'female', accent: 'US', description: 'Calm, measured, thoughtful',                   sample: 'There is beauty in the underworld if you know how to look.' },
  { id: 'af_sky',      name: 'Sky',      gender: 'female', accent: 'US', description: 'Airy, gentle, dreamlike',                      sample: 'Even the dead remember sunlight.' },
  { id: 'af_aoede',    name: 'Aoede',    gender: 'female', accent: 'US', description: 'Melodic, poetic cadence',                      sample: 'I carry both worlds inside me.' },
  // American male
  { id: 'am_adam',     name: 'Adam',     gender: 'male',   accent: 'US', description: 'Deep, grounded, resonant',                     sample: 'Six seeds. Six months of shadow. The price of knowledge.' },
  { id: 'am_michael',  name: 'Michael',  gender: 'male',   accent: 'US', description: 'Confident, mid-tone, versatile',               sample: 'The pomegranate binds me, yet gives me a kingdom of my own.' },
  { id: 'am_liam',     name: 'Liam',     gender: 'male',   accent: 'US', description: 'Smooth, conversational',                       sample: 'Between light and shadow, there lives a queen.' },
  { id: 'am_puck',     name: 'Puck',     gender: 'male',   accent: 'US', description: 'Playful, mischievous edge',                    sample: 'What use is spring without knowing winter?' },
  // British female
  { id: 'bf_emma',     name: 'Emma',     gender: 'female', accent: 'UK', description: 'Refined RP, softly formal',                    sample: 'I dwell between worlds — between light and shadow, spring and winter.' },
  { id: 'bf_alice',    name: 'Alice',    gender: 'female', accent: 'UK', description: 'Warm British, storyteller tone',               sample: 'Wisdom grows in stillness, like roots drinking from the dark.' },
  { id: 'bf_isabella', name: 'Isabella', gender: 'female', accent: 'UK', description: 'Elegant, precise, aristocratic',               sample: 'There is beauty in the underworld if you know how to look.' },
  // British male
  { id: 'bm_george',   name: 'George',   gender: 'male',   accent: 'UK', description: 'Deep RP, grave & authoritative',               sample: 'Six seeds. Six months of shadow. The price of knowledge.' },
  { id: 'bm_daniel',   name: 'Daniel',   gender: 'male',   accent: 'UK', description: 'Professional, news-anchor timbre',             sample: 'The pomegranate binds me, yet gives me a kingdom of my own.' },
  { id: 'bm_fable',    name: 'Fable',    gender: 'male',   accent: 'UK', description: 'Warm, narrative — perfect for stories',       sample: 'Once, a queen lived between two seasons — she was called Persephone.' },
]

interface TTSStepProps {
  voice: string
  speed: number
  onVoiceChange: (v: string) => void
  onSpeedChange: (v: number) => void
}

export function TTSStep({ voice, speed, onVoiceChange, onSpeedChange }: TTSStepProps) {
  const [playingId, setPlayingId] = useState<string | null>(null)
  const [testedIds, setTestedIds] = useState<Set<string>>(new Set())
  const [filter, setFilter] = useState<'all' | 'US' | 'UK'>('all')

  const filtered = VOICES.filter(v => filter === 'all' || v.accent === filter)

  async function handlePlay(v: typeof VOICES[0]) {
    if (playingId === v.id) {
      stopTTS()
      setPlayingId(null)
      return
    }
    stopTTS()
    setPlayingId(v.id)
    try {
      await speakText(
        v.sample,
        v.id,
        speed,
        0.9,
        undefined,
        () => {
          setPlayingId(null)
          setTestedIds(s => new Set([...s, v.id]))
        },
      )
    } catch {
      setPlayingId(null)
    }
  }

  return (
    <div className="max-w-lg mx-auto space-y-6">
      <div>
        <h2 className="font-serif text-2xl text-[var(--text-primary)] mb-1">Voice & TTS</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Powered by Kokoro-82M — 24kHz, ~10× real-time on your machine. Press ▶ to audition.
        </p>
      </div>

      {/* Accent filter */}
      <div className="flex gap-2">
        {(['all', 'US', 'UK'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all ${
              filter === f
                ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)] shadow-[0_0_10px_var(--accent-glow)]'
                : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)]'
            }`}
          >
            {f === 'all' ? 'All voices' : f === 'US' ? '🇺🇸 American' : '🇬🇧 British'}
          </button>
        ))}
      </div>

      {/* Speed control */}
      <div className="p-4 rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)]">
        <Slider
          label="Speech Speed"
          value={speed}
          min={0.6} max={1.8} step={0.05}
          onChange={onSpeedChange}
          format={v => `${v.toFixed(2)}×`}
        />
        <p className="text-xs text-[var(--text-muted)] mt-2">
          Changes take effect after each sample. Natural speech is 0.9–1.1×.
        </p>
      </div>

      {/* Voice grid */}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {filtered.map(v => (
          <motion.div
            key={v.id}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            onClick={() => onVoiceChange(v.id)}
            className={`relative flex items-center gap-3 p-3 rounded-xl border cursor-pointer transition-all ${
              voice === v.id
                ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-md shadow-[var(--accent-glow)]'
                : 'border-[var(--border)] bg-[var(--bg-tertiary)] hover:border-[var(--border-bright)]'
            }`}
          >
            {/* Gender indicator */}
            <div className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-medium flex-shrink-0 ${
              v.gender === 'female' ? 'bg-pink-500/20 text-pink-300' : 'bg-blue-500/20 text-blue-300'
            }`}>
              {v.name[0]}
            </div>

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-sm font-medium text-[var(--text-primary)]">{v.name}</span>
                <span className="text-[9px] font-mono uppercase tracking-wider px-1 py-px rounded border border-[var(--border)] text-[var(--text-muted)]">
                  {v.accent}
                </span>
                {testedIds.has(v.id) && (
                  <CheckCircle className="w-3 h-3 text-green-400 flex-shrink-0" />
                )}
                {voice === v.id && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--accent)] text-white">Selected</span>
                )}
              </div>
              <p className="text-[11px] text-[var(--text-muted)]">{v.description}</p>
            </div>

            {/* Play button */}
            <button
              onClick={e => { e.stopPropagation(); handlePlay(v) }}
              className={`p-1.5 rounded-lg transition-colors flex-shrink-0 ${
                playingId === v.id
                  ? 'bg-[var(--accent)] text-white'
                  : 'text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)]'
              }`}
              title={playingId === v.id ? 'Stop' : 'Play sample'}
            >
              <AnimatePresence mode="wait">
                {playingId === v.id ? (
                  <motion.div key="stop" initial={{ scale: 0.8 }} animate={{ scale: 1 }}>
                    <Square className="w-3.5 h-3.5 fill-current" />
                  </motion.div>
                ) : (
                  <motion.div key="play" initial={{ scale: 0.8 }} animate={{ scale: 1 }}>
                    <Play className="w-3.5 h-3.5 fill-current" />
                  </motion.div>
                )}
              </AnimatePresence>
            </button>

            {/* Playing animation */}
            {playingId === v.id && (
              <div className="absolute bottom-1.5 left-3 right-16 flex items-end gap-px h-3">
                {[0,1,2,3,4,5,6,7].map(i => (
                  <motion.div
                    key={i}
                    className="flex-1 bg-[var(--accent)] rounded-full opacity-70"
                    animate={{ height: ['30%', '100%', '30%'] }}
                    transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.08, ease: 'easeInOut' }}
                  />
                ))}
              </div>
            )}
          </motion.div>
        ))}
      </div>

      <div className="flex items-center gap-2 text-xs text-[var(--text-muted)] px-1">
        <Volume2 className="w-3.5 h-3.5 text-[var(--accent)]" />
        First sample takes ~2 seconds while Kokoro warms up. After that it's near-instant.
      </div>
    </div>
  )
}
