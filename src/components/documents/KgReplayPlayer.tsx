/**
 * Knowledge graph → guided Replay player (lazy-loaded on the first ▶ Replay).
 *
 * Renders, inside the graph stage: a floating control bar (play/pause, step,
 * progress, speed, narration, stop) and a callout card anchored next to the
 * active node. It owns the step clock and the optional narration (Kokoro TTS
 * via tts.ts, the next step's audio prefetched while the current one plays);
 * the graph view owns highlighting and camera moves (it gets `onStep`).
 *
 * Keyboard while active: Space play/pause · ← / → step · Esc stop (Esc is
 * swallowed so an enclosing modal stays open).
 *
 * After the last step (its time and narration done, or → on the last step) the
 * replay finishes: `onFinish` lets the view zoom out to the whole graph and
 * un-dim it, the callout goes away and the bar shrinks to "Replay finished ·
 * Replay again · Close" (it closes itself after END_AUTOCLOSE_MS if untouched).
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Play, Pause, ChevronLeft, ChevronRight, RotateCcw, Volume2, VolumeX, X, Check } from 'lucide-react'
import { clsx } from 'clsx'
import { useAppStore } from '@/store/appStore'
import { enqueueTTS, prefetchTTS, stopTTS } from '@/lib/tts'
import { stepDurationMs, type ReplayStep, type ReplayChip, type ReplayTone } from './kgReplay'


const NARRATE_KEY = 'persephone-kg-replay-narrate'
const SPEECH_WATCHDOG_MS = 30000
/** The finished bar closes itself after this long unless the user touches it. */
const END_AUTOCLOSE_MS = 6000

function readNarratePref(): boolean {
  try {
    return localStorage.getItem(NARRATE_KEY) === '1'
  } catch {
    return false
  }
}
function writeNarratePref(v: boolean) {
  try {
    localStorage.setItem(NARRATE_KEY, v ? '1' : '0')
  } catch {
    // ignore
  }
}

const TONE_COLOR: Record<ReplayTone, string> = {
  ok: 'rgb(16 185 129)',
  warn: 'rgb(245 158 11)',
  bad: 'rgb(239 68 68)',
  info: 'var(--accent)',
  neutral: 'var(--accent)',
}
const CHIP_COLOR: Record<NonNullable<ReplayChip['tone']>, string> = {
  model: 'var(--accent)',
  ok: 'rgb(16 185 129)',
  warn: 'rgb(245 158 11)',
  bad: 'rgb(239 68 68)',
  info: '#8b7cf6',
  muted: 'var(--text-muted)',
}

export interface ReplayPlayerProps {
  /** Steps for the current view (already filtered). */
  steps: ReplayStep[]
  /** Graph stage element (position: relative) — the callout anchors to nodes inside it. */
  stage: HTMLElement | null
  /** Node ids to anchor the callout to, per step (first found wins). */
  anchorIds: (step: ReplayStep) => string[]
  /** Run graph: shared nodes are drawn as `<id>@<runKey>` in Layers. */
  runKey?: string | null
  /** Short label for the bar ("Replaying run", "Fact story"). */
  label: string
  onStep: (index: number) => void
  /** Stop / Esc / Close (the view decides what to restore). */
  onExit: () => void
  /** The last step is done: the view shows the whole graph again. */
  onFinish?: () => void
}

interface Ctl {
  index: number
  playing: boolean
  hover: boolean
  speed: number
  narrate: boolean
  tts: 'unknown' | 'ok' | 'unavailable'
  token: number
  timer: ReturnType<typeof setTimeout> | null
  timerStart: number
  remaining: number
  timerDone: boolean
  speechDone: boolean
  usedSpeech: boolean
  watchdog: ReturnType<typeof setTimeout> | null
  ended: boolean
}

export function ReplayPlayer({ steps, stage, anchorIds, runKey, label, onStep, onExit, onFinish }: ReplayPlayerProps) {
  const [index, setIndex] = useState(0)
  const [playing, setPlaying] = useState(true)
  const [ended, setEnded] = useState(false)
  const [speed, setSpeed] = useState<1 | 2>(1)
  const [narrate, setNarrate] = useState<boolean>(readNarratePref)
  const [ttsState, setTtsState] = useState<Ctl['tts']>('unknown')
  const [narrow, setNarrow] = useState(false)
  useEffect(() => {
    if (!stage) return
    const measure = () => setNarrow(stage.clientWidth < 560)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(stage)
    return () => ro.disconnect()
  }, [stage])

  const stepsRef = useRef(steps)
  stepsRef.current = steps
  const onStepRef = useRef(onStep)
  onStepRef.current = onStep
  const onExitRef = useRef(onExit)
  onExitRef.current = onExit
  const onFinishRef = useRef(onFinish)
  onFinishRef.current = onFinish

  const ctlRef = useRef<Ctl | null>(null)
  if (!ctlRef.current) {
    ctlRef.current = {
      index: 0,
      playing: true,
      hover: false,
      speed: 1,
      narrate,
      tts: 'unknown',
      token: 0,
      timer: null,
      timerStart: 0,
      remaining: 0,
      timerDone: false,
      speechDone: true,
      usedSpeech: false,
      watchdog: null,
      ended: false,
    }
  }

  // Imperative controller: timers and speech callbacks read the ref, never stale state.
  const api = useMemo(() => {
    const S = ctlRef.current!
    const clearTimers = () => {
      if (S.timer) clearTimeout(S.timer)
      if (S.watchdog) clearTimeout(S.watchdog)
      S.timer = null
      S.watchdog = null
    }
    const silence = () => {
      if (S.usedSpeech) stopTTS()
    }
    const runTimer = () => {
      if (S.timer) clearTimeout(S.timer)
      S.timerStart = Date.now()
      S.timer = setTimeout(() => {
        S.timer = null
        S.remaining = 0
        S.timerDone = true
        maybeAdvance()
      }, Math.max(0, S.remaining))
    }
    const haltTimer = () => {
      if (!S.timer) return
      clearTimeout(S.timer)
      S.timer = null
      S.remaining = Math.max(0, S.remaining - (Date.now() - S.timerStart))
    }
    const speak = (i: number, token: number) => {
      const step = stepsRef.current[i]
      if (!step?.narration) {
        S.speechDone = true
        return
      }
      const tts = useAppStore.getState().settings.tts
      S.usedSpeech = true
      stopTTS()
      prefetchTTS(step.narration, tts.voice, tts.speed).then(ok => {
        if (token !== S.token) return
        if (!ok) {
          S.tts = 'unavailable'
          setTtsState('unavailable')
          S.speechDone = true
          maybeAdvance()
          return
        }
        if (S.tts !== 'ok') {
          S.tts = 'ok'
          setTtsState('ok')
        }
        const done = () => {
          if (token !== S.token || S.speechDone) return
          if (S.watchdog) clearTimeout(S.watchdog)
          S.watchdog = null
          S.speechDone = true
          maybeAdvance()
        }
        enqueueTTS(step.narration, tts.voice, tts.speed, tts.volume, undefined, done)
        if (S.watchdog) clearTimeout(S.watchdog)
        S.watchdog = setTimeout(done, SPEECH_WATCHDOG_MS)
        // Warm the next step's audio while this one plays (no gap between steps).
        const next = stepsRef.current[i + 1]
        if (next?.narration) void prefetchTTS(next.narration, tts.voice, tts.speed)
      })
    }
    const goTo = (target: number) => {
      const n = stepsRef.current.length
      if (n === 0) return
      const i = Math.max(0, Math.min(n - 1, target))
      S.index = i
      S.token++
      S.ended = false
      clearTimers()
      S.remaining = stepDurationMs(stepsRef.current[i], S.speed)
      S.timerDone = false
      setIndex(i)
      setEnded(false)
      onStepRef.current(i)
      const wantSpeech = S.narrate && S.tts !== 'unavailable'
      S.speechDone = !wantSpeech
      if (wantSpeech) speak(i, S.token)
      else silence()
      if (S.playing && !S.hover) runTimer()
    }
    // Past the last step: stop the clock / speech and hand over to the overview.
    const finish = () => {
      if (S.ended) return
      S.token++
      clearTimers()
      silence()
      S.timerDone = true
      S.speechDone = true
      S.playing = false
      S.hover = false // the callout disappears — a pending mouseleave would never come
      S.ended = true
      setPlaying(false)
      setEnded(true)
      onFinishRef.current?.()
    }
    const maybeAdvance = () => {
      if (!S.playing || S.hover || !S.timerDone || !S.speechDone) return
      if (S.index >= stepsRef.current.length - 1) {
        finish()
        return
      }
      goTo(S.index + 1)
    }
    const pause = () => {
      if (!S.playing) return
      S.playing = false
      setPlaying(false)
      haltTimer()
      S.token++ // drop pending speech callbacks
      if (S.watchdog) clearTimeout(S.watchdog)
      S.watchdog = null
      silence()
    }
    const play = () => {
      if (S.playing) return
      S.playing = true
      setPlaying(true)
      if (S.ended) {
        // Finished: start over.
        S.hover = false
        goTo(0)
        return
      }
      const wantSpeech = S.narrate && S.tts !== 'unavailable'
      S.speechDone = !wantSpeech
      if (wantSpeech) speak(S.index, S.token)
      if (!S.hover) {
        if (S.timerDone) maybeAdvance()
        else runTimer()
      }
    }
    return {
      goTo,
      play,
      pause,
      toggle: () => (S.playing ? pause() : play()),
      next: () => {
        if (S.ended) return
        if (S.index < stepsRef.current.length - 1) goTo(S.index + 1)
        else finish() // → on the last step: straight to the overview
      },
      // From the finished state, ← goes back to the last step (paused).
      prev: () => goTo(S.ended ? S.index : S.index - 1),
      restart: () => {
        S.playing = true
        S.hover = false
        setPlaying(true)
        goTo(0)
      },
      setSpeed: (v: 1 | 2) => {
        if (v === S.speed) return
        const wasRunning = !!S.timer
        haltTimer()
        S.remaining = (S.remaining * S.speed) / v
        S.speed = v
        setSpeed(v)
        if (wasRunning && S.playing && !S.hover && !S.timerDone) runTimer()
      },
      setNarrate: (v: boolean) => {
        S.narrate = v
        setNarrate(v)
        writeNarratePref(v)
        if (v && S.tts !== 'unavailable') {
          S.speechDone = false
          speak(S.index, S.token)
        } else {
          S.speechDone = true
          if (S.watchdog) clearTimeout(S.watchdog)
          S.watchdog = null
          silence()
          maybeAdvance()
        }
      },
      hover: (h: boolean) => {
        S.hover = h
        if (h) haltTimer()
        else if (S.playing) {
          if (S.timerDone) maybeAdvance()
          else runTimer()
        }
      },
      markUnavailable: () => {
        if (S.tts === 'ok') return
        S.tts = 'unavailable'
        setTtsState('unavailable')
        if (!S.speechDone) {
          S.speechDone = true
          maybeAdvance()
        }
      },
      dispose: () => {
        S.token++
        clearTimers()
        silence()
        S.usedSpeech = false
      },
      exit: () => {
        S.token++
        clearTimers()
        silence()
        S.usedSpeech = false
        onExitRef.current()
      },
    }
  }, [])

  // Start at step 1; clean up timers / speech when the replay unmounts.
  useEffect(() => {
    const S = ctlRef.current!
    S.playing = true
    S.hover = false
    api.goTo(0)
    return () => api.dispose()
  }, [api])

  // Is Kokoro TTS available at all? (disables the narration toggle when not)
  useEffect(() => {
    let cancelled = false
    fetch('/api/tts/voices')
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (cancelled) return
        if (!d || !Array.isArray(d.voices) || d.voices.length === 0) api.markUnavailable()
      })
      .catch(() => {
        if (!cancelled) api.markUnavailable()
      })
    return () => {
      cancelled = true
    }
  }, [api])

  // Keyboard: Space play/pause, ←/→ step, Esc stop. Capture phase so Esc never
  // reaches the modal's own Esc-to-close handler.
  useEffect(() => {
    const isTyping = (t: EventTarget | null) => {
      const el = t as HTMLElement | null
      return !!el?.closest?.('input, textarea, select, [contenteditable=""], [contenteditable="true"]')
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented && e.key !== 'Escape') return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        e.stopImmediatePropagation()
        api.exit()
        return
      }
      if (isTyping(e.target)) return
      if (e.key === ' ' || e.code === 'Space') {
        e.preventDefault()
        e.stopPropagation()
        if (!e.repeat) api.toggle()
      } else if (e.key === 'ArrowRight') {
        e.preventDefault()
        e.stopPropagation()
        api.next()
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault()
        e.stopPropagation()
        api.prev()
      }
    }
    const onKeyUp = (e: KeyboardEvent) => {
      // Space on a focused button would "click" it on keyup — the bar handles Space itself.
      if ((e.key === ' ' || e.code === 'Space') && !isTyping(e.target)) e.preventDefault()
    }
    window.addEventListener('keydown', onKey, true)
    window.addEventListener('keyup', onKeyUp, true)
    return () => {
      window.removeEventListener('keydown', onKey, true)
      window.removeEventListener('keyup', onKeyUp, true)
    }
  }, [api])

  // Finished: the bar closes itself after a few seconds unless the user touches it.
  const [endTouched, setEndTouched] = useState(false)
  useEffect(() => {
    if (!ended) {
      setEndTouched(false)
      return
    }
    if (endTouched) return
    const t = setTimeout(() => api.exit(), END_AUTOCLOSE_MS)
    return () => clearTimeout(t)
  }, [ended, endTouched, api])

  const step = steps[Math.min(index, steps.length - 1)]
  const total = steps.length
  const ttsOff = ttsState === 'unavailable'
  const onHover = useCallback((h: boolean) => api.hover(h), [api])
  if (!step) return null

  if (ended) {
    const touch = () => setEndTouched(true)
    return (
      <div
        className="kg-rp-bar kg-rp-bar--ended"
        role="toolbar"
        aria-label="Replay finished"
        onMouseDown={e => e.stopPropagation()}
        onMouseEnter={touch}
        onFocus={touch}
      >
        <div className="kg-rp-bar__row">
          <span className="kg-rp-bar__done" aria-live="polite">
            <Check className="w-3.5 h-3.5" />
            Replay finished
          </span>
          <button type="button" className="kg-rp-btn kg-rp-btn--text" onClick={api.restart} title="Replay again (Space)">
            <RotateCcw className="w-3.5 h-3.5" />
            Replay again
          </button>
          <button type="button" className="kg-rp-btn kg-rp-btn--text kg-rp-btn--stop" onClick={api.exit} title="Close (Esc)">
            <X className="w-3.5 h-3.5" />
            Close
          </button>
        </div>
      </div>
    )
  }

  return (
    <>
      <div className={clsx('kg-rp-bar', narrow && 'kg-rp-narrow')} role="toolbar" aria-label="Replay controls" onMouseDown={e => e.stopPropagation()}>
        <div className="kg-rp-bar__row">
          <button
            type="button"
            className="kg-rp-btn kg-rp-btn--primary"
            onClick={api.toggle}
            title={playing ? 'Pause (Space)' : 'Play (Space)'}
            aria-label={playing ? 'Pause' : 'Play'}
          >
            {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
          </button>
          <button type="button" className="kg-rp-btn" onClick={api.prev} disabled={index === 0} title="Previous step (←)" aria-label="Previous step">
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>
          <button type="button" className="kg-rp-btn" onClick={api.next} title={index >= total - 1 ? 'Finish (→)' : 'Next step (→)'} aria-label={index >= total - 1 ? 'Finish' : 'Next step'}>
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
          <span className="kg-rp-bar__count" aria-live="polite">
            {index + 1} / {total}
          </span>
          <span className="kg-rp-bar__label" title={label}>
            {step.title}
          </span>
          <button
            type="button"
            className="kg-rp-btn kg-rp-btn--text"
            onClick={() => api.setSpeed(speed === 1 ? 2 : 1)}
            title="Playback speed"
            aria-label={`Speed ${speed}×`}
          >
            {speed}×
          </button>
          <button
            type="button"
            className={clsx('kg-rp-btn kg-rp-btn--text', narrate && !ttsOff && 'is-on')}
            onClick={() => api.setNarrate(!narrate)}
            disabled={ttsOff}
            aria-pressed={narrate && !ttsOff}
            title={ttsOff ? 'Narration unavailable — the local voice (Kokoro TTS) is not running' : narrate ? 'Narration on' : 'Narrate each step aloud'}
          >
            {narrate && !ttsOff ? <Volume2 className="w-3.5 h-3.5" /> : <VolumeX className="w-3.5 h-3.5" />}
            <span className="kg-rp-hide-narrow">Narrate</span>
          </button>
          <button type="button" className="kg-rp-btn kg-rp-btn--text kg-rp-btn--stop" onClick={api.exit} title="Stop replay (Esc)" aria-label="Stop replay">
            <X className="w-3.5 h-3.5" />
            <span className="kg-rp-hide-narrow">Stop</span>
          </button>
        </div>
        <div className="kg-rp-bar__progress" aria-hidden>
          <span style={{ width: `${((index + 1) / total) * 100}%` }} />
        </div>
      </div>
      <ReplayCallout step={step} index={index} total={total} stage={stage} ids={anchorIds(step)} runKey={runKey} onHover={onHover} />
    </>
  )
}

// ── Callout ───────────────────────────────────────────────────────────────────

type Side = 'right' | 'left' | 'below' | 'above' | 'none'
interface Placement {
  x: number
  y: number
  side: Side
  caret: number
}

const BAR_INSET = 58
const MARGIN = 10
const GAP = 16

/** The step's visible nodes (stage coordinates): the first one found, and the union of all. */
function findAnchor(stage: HTMLElement, ids: string[], runKey?: string | null): { primary: DOMRect; union: DOMRect } | null {
  const sr = stage.getBoundingClientRect()
  let primary: DOMRect | null = null
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  for (const id of ids) {
    const esc = (v: string) => (typeof CSS !== 'undefined' && CSS.escape ? CSS.escape(v) : v.replace(/"/g, '\\"'))
    const el =
      stage.querySelector(`.react-flow__node[data-id="${esc(id)}"]`) ||
      (runKey ? stage.querySelector(`.react-flow__node[data-id="${esc(`${id}@${runKey}`)}"]`) : null)
    if (!el) continue
    const r = el.getBoundingClientRect()
    if (r.width === 0 && r.height === 0) continue
    // Must be at least partly inside the stage.
    if (r.right < sr.left || r.left > sr.right || r.bottom < sr.top + BAR_INSET || r.top > sr.bottom) continue
    const rel = new DOMRect(r.left - sr.left, r.top - sr.top, r.width, r.height)
    if (!primary) primary = rel
    x0 = Math.min(x0, rel.left)
    y0 = Math.min(y0, rel.top)
    x1 = Math.max(x1, rel.right)
    y1 = Math.max(y1, rel.bottom)
  }
  return primary ? { primary, union: new DOMRect(x0, y0, x1 - x0, y1 - y0) } : null
}

function place(anchor: { primary: DOMRect; union: DOMRect } | null, cw: number, ch: number, W: number, H: number): Placement {
  const clampY = (y: number) => Math.max(BAR_INSET, Math.min(H - ch - MARGIN, y))
  const clampX = (x: number) => Math.max(MARGIN, Math.min(W - cw - MARGIN, x))
  if (!anchor) return { x: clampX((W - cw) / 2), y: Math.max(BAR_INSET, H - ch - 16), side: 'none', caret: 0 }
  // Beside all of the step's nodes when there is room (never covering one of them),
  // else beside the primary node. Order: right, below, above, left — the camera
  // reserves room on the right (wide stages) or below, and graphs flow left→right,
  // so a callout on the left would cover the edge / previous step leading in.
  const u = anchor.union
  const p = anchor.primary
  if (u.right + GAP + cw <= W - MARGIN) {
    const y = clampY(p.y + p.height / 2 - ch / 2)
    return { x: u.right + GAP, y, side: 'right', caret: Math.max(16, Math.min(ch - 16, p.y + p.height / 2 - y)) }
  }
  // Below / above all of them.
  const pcx = p.x + p.width / 2
  if (u.bottom + GAP + ch <= H - MARGIN) {
    const x = clampX(pcx - cw / 2)
    return { x, y: u.bottom + GAP, side: 'below', caret: Math.max(16, Math.min(cw - 16, pcx - x)) }
  }
  if (u.y - GAP - ch >= BAR_INSET) {
    const x = clampX(pcx - cw / 2)
    return { x, y: u.y - GAP - ch, side: 'above', caret: Math.max(16, Math.min(cw - 16, pcx - x)) }
  }
  if (u.x - GAP - cw >= MARGIN) {
    const y = clampY(p.y + p.height / 2 - ch / 2)
    return { x: u.x - GAP - cw, y, side: 'left', caret: Math.max(16, Math.min(ch - 16, p.y + p.height / 2 - y)) }
  }
  const a = p
  const cx = a.x + a.width / 2
  const cy = a.y + a.height / 2
  // Right / left: beside the node.
  if (a.right + GAP + cw <= W - MARGIN) {
    const y = clampY(cy - ch / 2)
    return { x: a.right + GAP, y, side: 'right', caret: Math.max(16, Math.min(ch - 16, cy - y)) }
  }
  if (a.x - GAP - cw >= MARGIN) {
    const y = clampY(cy - ch / 2)
    return { x: a.x - GAP - cw, y, side: 'left', caret: Math.max(16, Math.min(ch - 16, cy - y)) }
  }
  // Below / above.
  if (a.bottom + GAP + ch <= H - MARGIN) {
    const x = clampX(cx - cw / 2)
    return { x, y: a.bottom + GAP, side: 'below', caret: Math.max(16, Math.min(cw - 16, cx - x)) }
  }
  if (a.y - GAP - ch >= BAR_INSET) {
    const x = clampX(cx - cw / 2)
    return { x, y: a.y - GAP - ch, side: 'above', caret: Math.max(16, Math.min(cw - 16, cx - x)) }
  }
  // No room anywhere: dock at the bottom of the stage.
  return { x: clampX(cx - cw / 2), y: Math.max(BAR_INSET, H - ch - MARGIN), side: 'none', caret: 0 }
}

function ReplayCallout({
  step,
  index,
  total,
  stage,
  ids,
  runKey,
  onHover,
}: {
  step: ReplayStep
  index: number
  total: number
  stage: HTMLElement | null
  ids: string[]
  runKey?: string | null
  onHover: (h: boolean) => void
}) {
  const cardRef = useRef<HTMLDivElement | null>(null)
  const [pos, setPos] = useState<Placement | null>(null)
  const idsKey = ids.join('|')

  // Follow the anchor every frame (camera pans / zooms, resizes, layout changes).
  useLayoutEffect(() => {
    if (!stage) return
    let raf = 0
    let last = ''
    const tick = () => {
      const card = cardRef.current
      if (card) {
        const W = stage.clientWidth
        const H = stage.clientHeight
        const p = place(findAnchor(stage, ids, runKey), card.offsetWidth, card.offsetHeight, W, H)
        const k = `${Math.round(p.x)},${Math.round(p.y)},${p.side},${Math.round(p.caret)}`
        if (k !== last) {
          last = k
          setPos(p)
        }
      }
      raf = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(raf)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, idsKey, runKey, step.id])

  const color = TONE_COLOR[step.tone] || 'var(--accent)'
  return (
    <div
      ref={cardRef}
      className={clsx('kg-rp-callout', pos ? `is-${pos.side}` : 'is-measuring')}
      style={{
        transform: pos ? `translate(${Math.round(pos.x)}px, ${Math.round(pos.y)}px)` : undefined,
        ['--rp-tone' as string]: color,
        ['--rp-caret' as string]: `${pos?.caret ?? 0}px`,
      }}
      role="status"
      aria-live="polite"
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
    >
      <div key={step.id} className="kg-rp-callout__inner">
        <div className="kg-rp-callout__head">
          <span className="kg-rp-callout__eyebrow">{step.eyebrow}</span>
          <span className="kg-rp-callout__index">
            {index + 1}/{total}
          </span>
        </div>
        <div className="kg-rp-callout__title">{step.title}</div>
        <div className="kg-rp-callout__body">{step.body}</div>
        {step.chips.length > 0 && (
          <div className="kg-rp-callout__chips">
            {step.chips.slice(0, 6).map((c, i) => (
              <span key={`${c.label}-${i}`} className="kg-rp-chip" style={{ ['--chip' as string]: CHIP_COLOR[c.tone || 'muted'] }}>
                {c.label}
              </span>
            ))}
          </div>
        )}
      </div>
      {pos && pos.side !== 'none' && <span className="kg-rp-callout__caret" aria-hidden />}
    </div>
  )
}
