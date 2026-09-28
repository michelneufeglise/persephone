/**
 * Sentence-queued TTS. Assistant answers reach this via speakResponse() in
 * speech.ts (after streaming, summary or cleaned full text); voice previews
 * call enqueueTTS/speakText directly.
 *
 * Pipeline:
 *   1. enqueueTTS(sentence) — queues it; the head sentence is synthesised on
 *      its own, the next PREFETCH ones while it plays.
 *   2. A worker drains the queue: decodes audio, plays sequentially, drives onLevel.
 *   3. Subsequent fetches overlap playback — so when one sentence finishes,
 *      the next is already buffered, eliminating gaps between sentences.
 */

let audioContext: AudioContext | null = null
let currentSource: AudioBufferSourceNode | null = null

interface QueueItem {
  sessionId: number
  text: string
  voice: string
  speed: number
  volume: number
  /** Started lazily — at most PREFETCH items ahead of the one playing. */
  fetchPromise: Promise<ArrayBuffer | null> | null
}

// How many queued sentences may be synthesising ahead of the one currently
// playing. Firing every sentence at once (a whole answer arrives in one go
// since speech starts after streaming) makes Kokoro requests contend for CPU
// and for the browser's ~6 connections per host, which delays the FIRST
// sentence (≈1.3 s alone vs ≈3.5 s with three in parallel) and blocks other
// /api calls. The head sentence is synthesised alone; two are prefetched
// while it plays.
const PREFETCH = 2

function ensureFetch(item: QueueItem): Promise<ArrayBuffer | null> {
  if (!item.fetchPromise) {
    item.fetchPromise = prefetched.get(prefetchKey(item.text, item.voice, item.speed)) ?? fetchTTS(item.text, item.voice, item.speed)
  }
  return item.fetchPromise
}

// ── Explicit prefetch (knowledge-graph replay narration) ──────────────────────
// Audio synthesised ahead of time with prefetchTTS() is kept in a small LRU
// cache and used by the queue instead of a second /api/tts request, so the next
// replay step starts speaking without a gap. Nothing else fills this cache.
const PREFETCH_CACHE_MAX = 24
const prefetched = new Map<string, Promise<ArrayBuffer | null>>()

function prefetchKey(text: string, voice: string, speed: number): string {
  return `${voice}\u0000${speed}\u0000${text.trim()}`
}

/** Synthesise `text` now (cached) so a later enqueueTTS() of the same text plays at once.
 *  Resolves true when audio is available, false when synthesis failed. */
export function prefetchTTS(text: string, voice: string, speed: number): Promise<boolean> {
  const cleaned = text.trim()
  if (!cleaned) return Promise.resolve(false)
  const key = prefetchKey(cleaned, voice, speed)
  let p = prefetched.get(key)
  if (p) {
    prefetched.delete(key) // LRU bump
  } else {
    p = fetchTTS(cleaned, voice, speed)
    const mine = p
    // A failed synthesis is not cached (a retry may succeed).
    mine.then(buf => {
      if (!buf && prefetched.get(key) === mine) prefetched.delete(key)
    })
  }
  prefetched.set(key, p)
  while (prefetched.size > PREFETCH_CACHE_MAX) {
    const oldest = prefetched.keys().next().value
    if (oldest === undefined) break
    prefetched.delete(oldest)
  }
  return p.then(buf => !!buf)
}

function prefetchAhead() {
  for (let i = 0; i < Math.min(PREFETCH, queue.length); i++) ensureFetch(queue[i])
}

const queue: QueueItem[] = []
let workerRunning = false
let levelCb: ((level: number) => void) | null = null
let allDoneCb: (() => void) | null = null
let pendingDoneFire = false

// Monotonic session counter. Incremented by stopTTS() so any in-flight items
// from prior sessions are discarded by the worker the moment they resolve.
let currentSession = 0

function getAudioContext(): AudioContext {
  if (!audioContext || audioContext.state === 'closed') {
    audioContext = new AudioContext()
  }
  return audioContext
}

async function fetchTTS(text: string, voice: string, speed: number): Promise<ArrayBuffer | null> {
  try {
    const res = await fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice, speed }),
    })
    if (!res.ok) return null
    return await res.arrayBuffer()
  } catch {
    return null
  }
}

async function playBuffer(buf: ArrayBuffer, volume: number): Promise<void> {
  const ctx = getAudioContext()
  if (ctx.state === 'suspended') await ctx.resume()

  // decodeAudioData mutates the buffer on some platforms — use a copy
  const audioBuffer = await ctx.decodeAudioData(buf.slice(0))

  const gain = ctx.createGain()
  gain.gain.value = volume

  const analyser = ctx.createAnalyser()
  analyser.fftSize = 256
  const data = new Uint8Array(analyser.frequencyBinCount)

  const source = ctx.createBufferSource()
  source.buffer = audioBuffer
  source.connect(analyser)
  analyser.connect(gain)
  gain.connect(ctx.destination)
  currentSource = source

  let rafId = 0
  function tick() {
    analyser.getByteFrequencyData(data)
    const avg = data.reduce((a, b) => a + b, 0) / data.length
    levelCb?.(avg / 128)
    rafId = requestAnimationFrame(tick)
  }

  await new Promise<void>(resolve => {
    source.onended = () => {
      cancelAnimationFrame(rafId)
      if (currentSource === source) currentSource = null
      resolve()
    }
    source.start()
    tick()
  })
}

async function worker() {
  if (workerRunning) return
  workerRunning = true
  try {
    while (queue.length > 0) {
      const item = queue.shift()!
      // Synthesise the head sentence alone (fastest time-to-first-audio), then
      // prefetch the next ones while this one plays.
      const buf = await ensureFetch(item)
      if (item.sessionId === currentSession) prefetchAhead()
      // Drop items from a prior session — these were enqueued before stopTTS().
      if (!buf || item.sessionId !== currentSession) continue
      try { await playBuffer(buf, item.volume) } catch {}
    }
  } finally {
    workerRunning = false
    levelCb?.(0)
    if (pendingDoneFire) {
      pendingDoneFire = false
      allDoneCb?.()
    }
  }
}

/**
 * Enqueue one sentence. Its /api/tts request starts when it reaches the head
 * of the queue or falls within the PREFETCH window while earlier audio plays;
 * the audio plays after every earlier item has finished.
 */
export function enqueueTTS(
  text: string,
  voice: string,
  speed: number,
  volume: number,
  onLevel?: (level: number) => void,
  onAllDone?: () => void,
) {
  if (onLevel)    levelCb   = onLevel
  if (onAllDone)  allDoneCb = onAllDone

  const cleaned = text.trim()
  if (cleaned.length < 1) return

  queue.push({
    sessionId: currentSession,
    text: cleaned,
    voice,
    speed,
    volume,
    fetchPromise: null,
  })
  // While audio is already playing, keep the look-ahead window filled; an
  // idle queue is started by the worker (head sentence first, on its own).
  if (currentSource) prefetchAhead()

  pendingDoneFire = true
  worker()
}

/**
 * Stop all current and queued speech.
 * Bumps the session id so any in-flight fetches that resolve afterwards are
 * dropped by the worker instead of being decoded and played.
 */
export function stopTTS() {
  currentSession++
  queue.length = 0
  pendingDoneFire = false
  if (currentSource) {
    try { currentSource.stop() } catch {}
    currentSource = null
  }
  levelCb?.(0)
}

/** Current playback session id — changes every time stopTTS() runs. Lets
 *  async callers (e.g. speakResponse waiting on a summary) detect that they
 *  were cancelled or superseded before they enqueue audio. */
export function getTTSSession(): number {
  return currentSession
}

/** Split already-speakable text into sentences so the first one can be
 *  synthesised (and start playing) while the rest are still being fetched. */
export function splitIntoSentences(text: string): string[] {
  const cursor: SentenceCursor = { pos: 0 }
  const out = extractNewSentences(text, cursor)
  const tail = extractTail(text, cursor)
  if (tail) out.push(tail)
  return out
}

/** Convenience wrapper for single-shot synthesis of fixed phrases (voice tests
 *  and previews). Assistant responses go through speakResponse() in speech.ts. */
export async function speakText(
  text: string,
  voice: string,
  speed: number,
  volume: number,
  onLevel?: (level: number) => void,
  onDone?: () => void,
): Promise<void> {
  stopTTS()
  enqueueTTS(text, voice, speed, volume, onLevel, onDone)
}

// ── Sentence-boundary scanning for streaming output ──────────────────────────

/**
 * Strip markdown so the TTS engine doesn't read stars, brackets, or URLs aloud.
 */
export function cleanForTTS(s: string): string {
  return s
    // strip fenced code blocks entirely
    .replace(/```[\s\S]*?```/g, ' ')
    // inline code → just the content
    .replace(/`([^`]*)`/g, '$1')
    // images → alt text
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    // links → link text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    // emphasis markers
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*]+)\*/g, '$1')
    .replace(/__([^_]+)__/g, '$1')
    .replace(/_([^_]+)_/g, '$1')
    // headers
    .replace(/^#{1,6}\s+/gm, '')
    // blockquote markers
    .replace(/^>\s?/gm, '')
    // list bullets
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    // hr
    .replace(/^---+$/gm, '')
    .replace(/\s+/g, ' ')
    .trim()
}

const SENTENCE_END_RE = /[.!?](?=\s|$|["')\]])/g
// Don't break after common abbreviations
const ABBREVIATIONS = /(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|e\.g|i\.e|cf|approx|fig|no|vol|pp)\.$/i

export interface SentenceCursor {
  /** index in the source text up to which we've already emitted sentences */
  pos: number
}

/**
 * Scan accumulated text for newly-completed sentences past the cursor.
 * Returns the list of new sentences and advances the cursor in place.
 */
export function extractNewSentences(text: string, cursor: SentenceCursor): string[] {
  const out: string[] = []
  let search = cursor.pos
  let lastBreak = cursor.pos

  // Reset regex search position
  SENTENCE_END_RE.lastIndex = search
  let m: RegExpExecArray | null
  while ((m = SENTENCE_END_RE.exec(text)) !== null) {
    const endIdx = m.index + 1                       // include the punctuation
    const candidate = text.slice(lastBreak, endIdx)
    // Skip if it ends in a common abbreviation
    if (ABBREVIATIONS.test(candidate.trim())) {
      continue
    }
    const sentence = candidate.trim()
    if (sentence.length >= 1) {
      const cleaned = cleanForTTS(sentence)
      if (cleaned.length >= 2) {
        out.push(cleaned)
      }
    }
    lastBreak = endIdx
  }

  cursor.pos = lastBreak
  return out
}

/**
 * Emit any remaining text after the last sentence boundary (called when streaming ends).
 */
export function extractTail(text: string, cursor: SentenceCursor): string | null {
  if (cursor.pos >= text.length) return null
  const tail = text.slice(cursor.pos).trim()
  if (tail.length < 2) {
    cursor.pos = text.length
    return null
  }
  const cleaned = cleanForTTS(tail)
  cursor.pos = text.length
  return cleaned.length >= 2 ? cleaned : null
}
