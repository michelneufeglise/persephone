import { useEffect, useRef } from 'react'
import { useAppStore } from '@/store/appStore'

/* ─── theme colour helpers ───────────────────────────────────────────── */
function getCssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}
function hexToRgb(color: string): [number, number, number] {
  const rgbMatch = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/)
  if (rgbMatch) return [+rgbMatch[1], +rgbMatch[2], +rgbMatch[3]]
  const hex = color.replace('#', '')
  if (hex.length === 6) {
    return [
      parseInt(hex.slice(0, 2), 16),
      parseInt(hex.slice(2, 4), 16),
      parseInt(hex.slice(4, 6), 16),
    ]
  }
  if (hex.length === 3) {
    return [
      parseInt(hex[0] + hex[0], 16),
      parseInt(hex[1] + hex[1], 16),
      parseInt(hex[2] + hex[2], 16),
    ]
  }
  return [214, 53, 106]
}
function rgba(color: string, alpha: number): string {
  const [r, g, b] = hexToRgb(color)
  return `rgba(${r},${g},${b},${alpha})`
}
function mixColors(a: string, b: string, t: number): string {
  const [ar, ag, ab] = hexToRgb(a)
  const [br, bg, bb] = hexToRgb(b)
  const r = Math.round(ar + (br - ar) * t)
  const g = Math.round(ag + (bg - ag) * t)
  const bl = Math.round(ab + (bb - ab) * t)
  return `rgb(${r},${g},${bl})`
}

/* ── Config ───────────────────────────────────────────────────────────── */
const BAR_COUNT   = 72        // number of samples in the rolling history
const WIDTH       = 300
const HEIGHT      = 120
const MIN_BAR_H   = 4         // idle height so the waveform is never empty
const MAX_BAR_H   = HEIGHT * 0.88

/* Persistent per-bar noise phase so bars don't wobble in lockstep */
const NOISE_PHASE = Array.from({ length: BAR_COUNT }, () => Math.random() * Math.PI * 2)

/* ─────────────────────────────────────────────────────────────────────── */
export function VoiceOrb() {
  const { isSpeaking, isGenerating, audioLevel } = useAppStore()
  const canvasRef  = useRef<HTMLCanvasElement>(null)
  const rafRef     = useRef<number>(0)
  const phaseRef   = useRef(0)
  // Rolling history — bar[0] is oldest (drawn left), bar[BAR_COUNT-1] is newest (right)
  const historyRef = useRef<Float32Array>(new Float32Array(BAR_COUNT))
  // Smoothed intensity — attack-fast, release-slow so peaks pop and decay
  const smoothedRef = useRef(0)
  const targetRef   = useRef(0)

  useEffect(() => {
    targetRef.current = isSpeaking
      ? Math.max(0.15, audioLevel)
      : isGenerating
      ? 0.35
      : 0.08
  }, [isSpeaking, isGenerating, audioLevel])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx    = canvas.getContext('2d')!
    const DPR    = Math.min(window.devicePixelRatio || 1, 2)
    canvas.width  = WIDTH * DPR
    canvas.height = HEIGHT * DPR
    canvas.style.width  = `${WIDTH}px`
    canvas.style.height = `${HEIGHT}px`
    ctx.scale(DPR, DPR)

    function draw() {
      phaseRef.current += 0.08
      const phase = phaseRef.current

      // Ease smoothed toward target (attack fast, release slow — feels vocal)
      const target = targetRef.current
      const cur    = smoothedRef.current
      const rate   = target > cur ? 0.35 : 0.09
      smoothedRef.current = cur + (target - cur) * rate
      const level = smoothedRef.current

      // Push a new sample into the rolling history. The new sample is a
      // combination of the current amplitude and a per-frame noise burst
      // scaled by amplitude — makes the waveform feel organic even if the
      // driving audioLevel is quantized.
      const history = historyRef.current
      history.copyWithin(0, 1)
      // Blend two noise waves at different frequencies for character
      const noiseA = Math.sin(phase * 3.1)  * 0.35
      const noiseB = Math.sin(phase * 1.7 + 1.2) * 0.65
      const jitter = (noiseA + noiseB) * (0.3 + level * 0.7)
      history[BAR_COUNT - 1] = Math.max(0.02, level + jitter * 0.4)

      // Theme
      const c1 = getCssVar('--accent')     || '#d6356a'
      const c2 = getCssVar('--holo')       || '#7df9ff'
      const c3 = getCssVar('--gold')       || '#f0c060'
      const cDeep = getCssVar('--accent-deep') || '#3a0a2c'

      ctx.clearRect(0, 0, WIDTH, HEIGHT)

      /* ── 1. Background aurora — soft, amplitude-reactive ─────────────── */
      const midY = HEIGHT / 2
      const aurora = ctx.createRadialGradient(WIDTH / 2, midY, 20, WIDTH / 2, midY, WIDTH * 0.6)
      aurora.addColorStop(0,   rgba(c1, 0.14 + level * 0.30))
      aurora.addColorStop(0.5, rgba(c2, 0.06 + level * 0.15))
      aurora.addColorStop(1,   rgba(c1, 0))
      ctx.fillStyle = aurora
      ctx.fillRect(0, 0, WIDTH, HEIGHT)

      /* ── 2. Faint horizontal mid-line ─────────────────────────────────── */
      ctx.strokeStyle = rgba(c1, 0.15 + level * 0.2)
      ctx.lineWidth   = 0.6
      ctx.beginPath()
      ctx.moveTo(6, midY)
      ctx.lineTo(WIDTH - 6, midY)
      ctx.stroke()

      /* ── 3. The waveform bars ────────────────────────────────────────── */
      const barGap    = 1
      const barWidth  = (WIDTH - 24) / BAR_COUNT - barGap
      const leftPad   = 12

      for (let i = 0; i < BAR_COUNT; i++) {
        const sample = history[i]
        const px     = i / (BAR_COUNT - 1)     // 0 → 1 across the strip

        // Envelope so peaks live in the middle of the strip — like natural
        // speech which typically has stronger energy in the centre of an
        // utterance and quieter edges. sin(x*π) is a nice bell.
        const envelope = 0.35 + 0.65 * Math.sin(px * Math.PI)

        // Per-bar noise so idle waveform still breathes rather than sitting flat
        const idle = 0.06 + 0.05 * Math.sin(phase * 0.7 + NOISE_PHASE[i])

        // Combined height
        let h = Math.max(idle, sample) * envelope * MAX_BAR_H
        if (h < MIN_BAR_H) h = MIN_BAR_H

        const x = leftPad + i * (barWidth + barGap)
        const y = midY - h / 2

        // Colour: blend along X (accent → holo → accent) so the strip feels
        // like a spectrum, plus a gold flash on strong peaks.
        const along  = Math.abs(px - 0.5) * 2       // 0 centre → 1 edges
        const strong = sample > 0.6
        const barCol = strong
          ? c3
          : mixColors(c2, c1, along)                 // holo centre → accent edges

        // Gradient fill top→bottom for depth
        const grad = ctx.createLinearGradient(x, y, x, y + h)
        grad.addColorStop(0,   rgba(barCol, 0.35))
        grad.addColorStop(0.5, rgba(barCol, 0.95))
        grad.addColorStop(1,   rgba(barCol, 0.35))
        ctx.fillStyle = grad

        // Rounded rect (Canvas API roundRect is broadly supported since 2023)
        const r = Math.min(barWidth / 2, 3)
        ctx.beginPath()
        // Fallback path in case roundRect isn't available
        if ((ctx as any).roundRect) {
          (ctx as any).roundRect(x, y, barWidth, h, r)
        } else {
          ctx.rect(x, y, barWidth, h)
        }
        ctx.fill()
      }

      /* ── 4. Right-edge glow — "new sound is arriving here" ────────────── */
      const glowW = 40
      const edge  = ctx.createLinearGradient(WIDTH - glowW, 0, WIDTH, 0)
      edge.addColorStop(0, rgba(c1, 0))
      edge.addColorStop(1, rgba(c2, 0.25 + level * 0.35))
      ctx.fillStyle = edge
      ctx.fillRect(WIDTH - glowW, 0, glowW, HEIGHT)

      /* ── 5. Highlight spark on strong peaks — a moving vertical sheen ── */
      if (level > 0.5) {
        const sparkX = leftPad + (0.5 + 0.5 * Math.sin(phase * 0.3)) * (WIDTH - 24)
        const spark  = ctx.createRadialGradient(sparkX, midY, 4, sparkX, midY, 60)
        spark.addColorStop(0, rgba(c2, 0.35))
        spark.addColorStop(1, rgba(c2, 0))
        ctx.fillStyle = spark
        ctx.fillRect(0, 0, WIDTH, HEIGHT)
      }

      // (cDeep referenced to keep bundlers from tree-shaking it — used in
      // future rim variant; noop here.)
      void cDeep

      rafRef.current = requestAnimationFrame(draw)
    }

    draw()
    return () => cancelAnimationFrame(rafRef.current)
  }, [])

  return (
    <div
      className="relative flex items-center justify-center mx-auto"
      style={{ width: WIDTH, height: HEIGHT }}
    >
      <canvas
        ref={canvasRef}
        className="relative z-10"
        style={{
          filter:
            'drop-shadow(0 0 24px var(--accent-glow)) drop-shadow(0 0 60px var(--accent-glow))',
        }}
      />
    </div>
  )
}
