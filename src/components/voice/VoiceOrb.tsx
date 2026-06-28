import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { useAppStore } from '@/store/appStore'

function getCssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function hexToRgb(color: string): [number, number, number] {
  // Handle rgb/rgba
  const rgbMatch = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/)
  if (rgbMatch) return [+rgbMatch[1], +rgbMatch[2], +rgbMatch[3]]
  // Handle hex
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
  return [139, 34, 82] // fallback crimson
}

function rgba(color: string, alpha: number): string {
  const [r, g, b] = hexToRgb(color)
  return `rgba(${r},${g},${b},${alpha})`
}

function lighten(color: string, amount: number): string {
  const [r, g, b] = hexToRgb(color)
  return `rgb(${Math.min(255, Math.round(r + 255 * amount))},${Math.min(255, Math.round(g + 255 * amount))},${Math.min(255, Math.round(b + 255 * amount))})`
}

export function VoiceOrb() {
  const { isSpeaking, isGenerating, audioLevel } = useAppStore()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rafRef = useRef<number>(0)
  const phaseRef = useRef(0)

  const isActive = isSpeaking || isGenerating
  const intensity = isSpeaking ? audioLevel : isGenerating ? 0.3 : 0.05

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')!
    const SIZE = 200
    canvas.width = SIZE
    canvas.height = SIZE

    function draw() {
      phaseRef.current += 0.025
      const phase = phaseRef.current
      const cx = SIZE / 2
      const cy = SIZE / 2
      const baseR = 55
      const dynamicR = baseR + intensity * 28

      // Read theme colors each frame (cheap, only 3 calls)
      const c1 = getCssVar('--orb-color-1') || '#8b2252'
      const c2 = getCssVar('--orb-color-2') || '#4a0f6b'
      const c3 = getCssVar('--orb-color-3') || '#c4a644'

      ctx.clearRect(0, 0, SIZE, SIZE)

      // Outer glow rings
      for (let ring = 3; ring >= 1; ring--) {
        const ringR = dynamicR + ring * 18 + Math.sin(phase * 0.5 + ring) * (3 + intensity * 6)
        const alpha = (0.08 - ring * 0.02) * (0.4 + intensity * 0.6)
        const grad = ctx.createRadialGradient(cx, cy, ringR * 0.5, cx, cy, ringR)
        grad.addColorStop(0, rgba(c1, alpha))
        grad.addColorStop(1, rgba(c1, 0))
        ctx.beginPath()
        ctx.arc(cx, cy, ringR, 0, Math.PI * 2)
        ctx.fillStyle = grad
        ctx.fill()
      }

      // Core orb
      const gradient = ctx.createRadialGradient(
        cx - dynamicR * 0.3, cy - dynamicR * 0.3, 2,
        cx, cy, dynamicR,
      )
      gradient.addColorStop(0, lighten(c3, 0.5))
      gradient.addColorStop(0.35, c1)
      gradient.addColorStop(0.75, c2)
      gradient.addColorStop(1, rgba(c2, 0))

      // Organic distorted path
      ctx.beginPath()
      const pts = 64
      for (let i = 0; i <= pts; i++) {
        const angle = (i / pts) * Math.PI * 2
        const noise =
          Math.sin(angle * 3 + phase) * (2 + intensity * 8) +
          Math.sin(angle * 5 - phase * 1.3) * (1 + intensity * 5) +
          Math.sin(angle * 2 + phase * 0.7) * (1.5 + intensity * 4)
        const r = dynamicR + noise
        const x = cx + Math.cos(angle) * r
        const y = cy + Math.sin(angle) * r
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      }
      ctx.closePath()
      ctx.fillStyle = gradient
      ctx.fill()

      // Inner highlight
      const shimmer = ctx.createRadialGradient(
        cx - dynamicR * 0.2, cy - dynamicR * 0.25, 1,
        cx - dynamicR * 0.2, cy - dynamicR * 0.25, dynamicR * 0.65,
      )
      shimmer.addColorStop(0, `rgba(255,255,255,${0.10 + intensity * 0.15})`)
      shimmer.addColorStop(1, 'rgba(255,255,255,0)')
      ctx.beginPath()
      ctx.arc(cx, cy, dynamicR * 0.95, 0, Math.PI * 2)
      ctx.fillStyle = shimmer
      ctx.fill()

      rafRef.current = requestAnimationFrame(draw)
    }

    draw()
    return () => cancelAnimationFrame(rafRef.current)
  }, [intensity])

  return (
    <div className="relative flex items-center justify-center w-[200px] h-[200px] mx-auto">
      {isActive && (
        <motion.div
          className="absolute inset-0 rounded-full border border-[var(--accent)]"
          animate={{ opacity: [0.2, 0.6, 0.2], scale: [0.93, 1.07, 0.93] }}
          transition={{ duration: 2, repeat: Infinity, ease: 'easeInOut' }}
        />
      )}
      <canvas
        ref={canvasRef}
        className="relative z-10 drop-shadow-lg"
        style={{ borderRadius: '50%', filter: `drop-shadow(0 0 ${12 + intensity * 20}px var(--accent-glow))` }}
      />
    </div>
  )
}
