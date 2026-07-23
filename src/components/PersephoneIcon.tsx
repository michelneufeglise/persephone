// Served from /public — transparent-PNG line-art logo. Preferred over the
// old JPG medallion because it composites cleanly on any background.
const iconUrl = '/persephone_logo.png'
import { clsx } from 'clsx'

/**
 * The Persephone illustration rendered as a circular medallion — a warm
 * off-white disc holds the black Art Nouveau line-art so it reads
 * cleanly against every theme. Add a soft accent glow for depth.
 *
 * The image itself is set as a CSS background so we can crop it into the
 * disc without downloading twice for `<img>` + a decorative frame.
 */
interface PersephoneIconProps {
  /** Diameter in px (defaults 40). */
  size?:      number
  /** Adds an outer accent glow — turn OFF in dense UI to reduce noise. */
  glow?:      boolean
  /** Tighter crop for header-sized use (default true). */
  cropTight?: boolean
  className?: string
}

export function PersephoneIcon({
  size      = 40,
  glow      = true,
  cropTight = true,
  className,
}: PersephoneIconProps) {
  const px = `${size}px`
  return (
    <span
      className={clsx('relative inline-flex items-center justify-center flex-shrink-0', className)}
      style={{ width: px, height: px }}
      aria-label="Persephone"
    >
      {glow && (
        <span
          aria-hidden
          className="absolute inset-0 rounded-full blur-md opacity-70 pointer-events-none"
          style={{
            background:
              'radial-gradient(circle at 50% 50%, var(--accent-glow), transparent 65%)',
          }}
        />
      )}
      <span
        className="relative rounded-full overflow-hidden ring-1 ring-[var(--border-bright)]"
        style={{
          width:  px,
          height: px,
          // Warm cream disc — vintage-inscription vibe. Sits well on dark
          // themes AND the light Elysian / Spring themes.
          background: '#f6efe3',
          boxShadow:
            'inset 0 -2px 6px rgba(139, 34, 82, 0.2), inset 0 1px 0 rgba(255,255,255,0.6), 0 4px 14px -4px var(--accent-glow)',
        }}
      >
        <span
          aria-hidden
          className="absolute inset-0"
          style={{
            backgroundImage:   `url(${iconUrl})`,
            // Transparent PNG — sits centred with a little breathing room.
            // Contain keeps the full silhouette + skull + flowers visible.
            backgroundSize:    cropTight ? '82%' : 'contain',
            backgroundPosition: 'center 52%',
            backgroundRepeat:  'no-repeat',
          }}
        />
        {/* Subtle vignette to blend the black artwork into the warm disc edges */}
        <span
          aria-hidden
          className="absolute inset-0 pointer-events-none"
          style={{
            background:
              'radial-gradient(circle at 50% 50%, transparent 55%, rgba(139,34,82,0.08) 90%)',
          }}
        />
      </span>
    </span>
  )
}
