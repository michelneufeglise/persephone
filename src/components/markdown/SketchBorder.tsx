import { useEffect, useRef } from 'react'
// @ts-expect-error — roughjs ships its own .d.ts only for the bundled build
import rough from 'roughjs/bundled/rough.esm.js'

interface SketchBorderProps {
  children: React.ReactNode
  /** CSS color string (or var()) for the stroke */
  stroke?: string
  strokeWidth?: number
  roughness?: number
  fillStyle?: 'hachure' | 'solid' | 'none'
  fill?: string
  padding?: number
  className?: string
}

/**
 * Wraps its children in a hand-drawn-looking SVG rectangle border generated
 * with roughjs. The SVG re-renders on resize so the border tracks the
 * content's actual dimensions. Theme-aware by default (uses --accent).
 */
export function SketchBorder({
  children,
  stroke      = 'var(--accent)',
  strokeWidth = 1.5,
  roughness   = 1.6,
  fillStyle   = 'none',
  fill        = 'transparent',
  padding     = 12,
  className   = '',
}: SketchBorderProps) {
  const wrapRef = useRef<HTMLDivElement>(null)
  const svgRef  = useRef<SVGSVGElement>(null)

  useEffect(() => {
    const wrap = wrapRef.current
    const svg  = svgRef.current
    if (!wrap || !svg) return

    const draw = () => {
      const w = wrap.offsetWidth
      const h = wrap.offsetHeight
      if (w === 0 || h === 0) return
      svg.setAttribute('width', String(w))
      svg.setAttribute('height', String(h))
      svg.setAttribute('viewBox', `0 0 ${w} ${h}`)
      // Clear previous
      while (svg.firstChild) svg.removeChild(svg.firstChild)
      const rc = rough.svg(svg)
      // Resolve var() at runtime so theme switches work
      const resolved = stroke.startsWith('var(')
        ? getComputedStyle(wrap).getPropertyValue(
            stroke.match(/var\((--[\w-]+)\)/)?.[1] ?? '--accent',
          ).trim() || '#d6356a'
        : stroke
      const node = rc.rectangle(2, 2, w - 4, h - 4, {
        stroke:      resolved,
        strokeWidth, roughness,
        fillStyle, fill,
        bowing:      1.4,
      })
      svg.appendChild(node)
    }

    draw()
    const ro = new ResizeObserver(draw)
    ro.observe(wrap)
    return () => ro.disconnect()
  }, [stroke, strokeWidth, roughness, fillStyle, fill])

  return (
    <div ref={wrapRef} className={`relative ${className}`} style={{ padding }}>
      <svg
        ref={svgRef}
        className="absolute inset-0 pointer-events-none"
        style={{ width: '100%', height: '100%' }}
      />
      <div className="relative">{children}</div>
    </div>
  )
}
