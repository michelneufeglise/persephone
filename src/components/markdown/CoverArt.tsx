/**
 * Deterministic generative SVG cover for a research report.
 *
 * Same query → same cover (seeded by FNV-1a hash of the text). Composition
 * is a constellation of shapes — orbs, triangles, lines, arcs — coloured
 * from the active theme's palette. Pure SVG, no images.
 */

interface CoverArtProps {
  seed: string
  height?: number
}

/* tiny seedable PRNG (mulberry32) so the layout is reproducible per query */
function mulberry32(a: number) {
  return function () {
    a |= 0
    a = (a + 0x6D2B79F5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function fnv1a(s: string): number {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = (h * 16777619) >>> 0
  }
  return h
}

export function CoverArt({ seed, height = 140 }: CoverArtProps) {
  const W = 1000
  const H = height * (1000 / 600) // keep proportional viewBox, render shrinks
  const rng = mulberry32(fnv1a(seed || 'persephone'))

  // Palette references — resolved at render via CSS vars
  const accent = 'var(--accent)'
  const holo   = 'var(--holo)'
  const gold   = 'var(--gold)'
  const deep   = 'var(--accent-deep)'

  type Shape = JSX.Element
  const shapes: Shape[] = []

  // 1. backdrop glow ellipse
  const glowCx = 200 + rng() * 600
  const glowCy = H * (0.3 + rng() * 0.4)
  shapes.push(
    <ellipse key="glow" cx={glowCx} cy={glowCy} rx={420} ry={220}
      fill={accent} opacity="0.25" filter="url(#blur)" />,
  )
  shapes.push(
    <ellipse key="glow2" cx={W - glowCx * 0.8} cy={H * 0.6} rx={300} ry={180}
      fill={holo} opacity="0.18" filter="url(#blur)" />,
  )

  // 2. 4-6 floating orbs
  const n = 4 + Math.floor(rng() * 3)
  for (let i = 0; i < n; i++) {
    const cx  = 40 + rng() * (W - 80)
    const cy  = 20 + rng() * (H - 40)
    const r   = 8 + rng() * 36
    const col = [accent, holo, gold, deep][Math.floor(rng() * 4)]
    const op  = 0.55 + rng() * 0.35
    shapes.push(
      <circle key={`o${i}`} cx={cx} cy={cy} r={r}
        fill={col} opacity={op} />,
    )
    // thin ring around some orbs
    if (rng() > 0.5) {
      shapes.push(
        <circle key={`r${i}`} cx={cx} cy={cy} r={r + 6 + rng() * 8}
          fill="none" stroke={col} strokeWidth="1" opacity={0.45} />,
      )
    }
  }

  // 3. a few thin chord lines connecting random points
  const lineCount = 2 + Math.floor(rng() * 3)
  for (let i = 0; i < lineCount; i++) {
    const x1 = rng() * W, y1 = rng() * H
    const x2 = rng() * W, y2 = rng() * H
    shapes.push(
      <line key={`l${i}`} x1={x1} y1={y1} x2={x2} y2={y2}
        stroke={holo} strokeWidth="0.8" opacity="0.4"
        strokeDasharray={rng() > 0.5 ? '2 4' : undefined} />,
    )
  }

  // 4. a couple of triangles for sharper accent
  const triCount = 1 + Math.floor(rng() * 2)
  for (let i = 0; i < triCount; i++) {
    const cx = rng() * W, cy = rng() * H, sz = 12 + rng() * 26
    const angle = rng() * 360
    shapes.push(
      <polygon key={`t${i}`}
        points={`${cx},${cy - sz} ${cx - sz * 0.866},${cy + sz / 2} ${cx + sz * 0.866},${cy + sz / 2}`}
        transform={`rotate(${angle} ${cx} ${cy})`}
        fill="none" stroke={gold} strokeWidth="1.2" opacity="0.75" />,
    )
  }

  // 5. thin arc sweep across top
  const arcStart = 50 + rng() * 200
  shapes.push(
    <path key="arc"
      d={`M ${arcStart} 30 Q ${W / 2} ${-20 + rng() * 40} ${W - arcStart} 30`}
      stroke={accent} strokeWidth="1" fill="none" opacity="0.55" />,
  )

  return (
    <div className="relative w-full overflow-hidden rounded-2xl border border-[var(--border)]"
         style={{
           background: 'linear-gradient(135deg, var(--bg-secondary), var(--bg-tertiary))',
           boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.04), var(--shadow-soft)',
         }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid slice"
        className="block w-full"
        style={{ height }}
        aria-hidden
      >
        <defs>
          <filter id="blur" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="30" />
          </filter>
        </defs>
        {shapes}
        {/* grain overlay scribble */}
        <g opacity="0.15">
          {Array.from({ length: 16 }).map((_, i) => (
            <line
              key={`g${i}`} x1={rng() * W} y1={rng() * H}
              x2={rng() * W} y2={rng() * H}
              stroke="white" strokeWidth="0.4"
            />
          ))}
        </g>
      </svg>
      {/* gradient fade into content below */}
      <div className="absolute inset-x-0 bottom-0 h-12 pointer-events-none"
        style={{ background: 'linear-gradient(180deg, transparent, var(--bg-secondary))' }} />
    </div>
  )
}
