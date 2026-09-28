/**
 * Spoken responses — the ONE place assistant text becomes speech.
 *
 * Every screen that speaks an assistant answer (main chat auto-speak, the
 * per-message "Read aloud" button, the Documents agent) calls
 * `speakResponse()`. It asks the backend what to say
 * (POST /api/tts/speech-text → cleaned text, or a short spoken summary from a
 * small local model), then hands the result to the Kokoro queue in tts.ts
 * sentence by sentence so the first sentence starts playing quickly.
 *
 * Voice on/off lives in `settings.tts.enabled` (the same flag as the Voice
 * panel toggle); `setVoiceEnabled()` is the shared toggle action that also
 * stops any current playback when switching off.
 */
import { useAppStore } from '@/store/appStore'
import { enqueueTTS, stopTTS, cleanForTTS, getTTSSession, splitIntoSentences } from './tts'

export type SpokenMode = 'summary' | 'full'

export interface SpeakOpts {
  /** Assistant message id — lets the backend log / cache per message. */
  messageId?: string
  /** Language hint (BCP-47, e.g. "nl"). Backend auto-detects when omitted. */
  lang?: string
  /** true = automatic speak after a response finished (respects Auto-play). */
  auto?: boolean
}

export interface SpeechTextResult {
  speech: string
  mode: 'passthrough' | 'summary' | 'fallback'
  model: string | null
  ms: number
  cached?: boolean
}

// Bumped on every speakResponse() call so an older call that is still waiting
// on its summary knows it has been superseded.
let latestRequest = 0

function spokenModeSetting(): SpokenMode {
  const m = useAppStore.getState().settings.tts.spokenMode
  return m === 'full' ? 'full' : 'summary'
}

/** Ask the backend what to say for `text`. Never throws: falls back to a
 *  client-side markdown clean-up if the endpoint is unreachable. */
export async function fetchSpeechText(
  text: string,
  opts: { messageId?: string; lang?: string; mode?: SpokenMode } = {},
): Promise<SpeechTextResult> {
  const t0 = performance.now()
  try {
    const res = await fetch('/api/tts/speech-text', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        message_id: opts.messageId ?? null,
        lang: opts.lang ?? null,
        mode: opts.mode ?? spokenModeSetting(),
      }),
    })
    if (res.ok) {
      const data = (await res.json()) as SpeechTextResult
      if (typeof data?.speech === 'string') return data
    }
  } catch {
    /* fall through to the local clean-up */
  }
  return {
    speech: cleanForTTS(text.replace(/<think>[\s\S]*?(<\/think>|$)/gi, ' ')),
    mode: 'fallback',
    model: null,
    ms: Math.round(performance.now() - t0),
  }
}

/** Stop any current or pending speech and reset the speaking indicators. */
export function stopSpeaking() {
  latestRequest++
  stopTTS()
  const s = useAppStore.getState()
  s.setIsSpeaking(false)
  s.setAudioLevel(0)
}

/** The shared Voice on/off action (composer button, Voice panel, Settings). */
export function setVoiceEnabled(enabled: boolean) {
  const s = useAppStore.getState()
  const tts = s.settings.tts
  // Turning voice on from a toggle means "speak replies" — make sure auto-play
  // isn't silently off, otherwise the toggle would appear to do nothing.
  s.updateTTSSettings(enabled && tts.autoPlay === false ? { enabled, autoPlay: true } : { enabled })
  if (!enabled) stopSpeaking()
}

/**
 * Speak an assistant response (after streaming has finished).
 * Resolves once the audio is queued (not when playback ends).
 */
export async function speakResponse(text: string, opts: SpeakOpts = {}): Promise<void> {
  const store = useAppStore.getState()
  const tts = store.settings.tts
  if (!tts.enabled) return
  if (opts.auto && tts.autoPlay === false) return
  if (!text || !text.trim()) return

  // Replace whatever is playing now — one voice at a time.
  stopTTS()
  const myRequest = ++latestRequest
  const session = getTTSSession()
  store.setIsSpeaking(true)

  const result = await fetchSpeechText(text, { messageId: opts.messageId, lang: opts.lang })

  const superseded = myRequest !== latestRequest || session !== getTTSSession()
  const now = useAppStore.getState()
  if (superseded || !now.settings.tts.enabled) {
    // Only clear the indicator if nobody newer took over the voice.
    if (myRequest === latestRequest) { now.setIsSpeaking(false); now.setAudioLevel(0) }
    return
  }

  const sentences = splitIntoSentences(result.speech)
  if (sentences.length === 0) {
    now.setIsSpeaking(false)
    return
  }
  const cur = now.settings.tts
  const onLevel = (lvl: number) => useAppStore.getState().setAudioLevel(lvl)
  const onDone = () => {
    const s = useAppStore.getState()
    s.setIsSpeaking(false)
    s.setAudioLevel(0)
  }
  for (const s of sentences) {
    enqueueTTS(s, cur.voice, cur.speed, cur.volume, onLevel, onDone)
  }
}
