/** A small SVG ornament — three diamonds joined by a thin line. */
export function OrnamentalDivider() {
  return (
    <div className="flex items-center justify-center my-6 select-none">
      <svg width="120" height="14" viewBox="0 0 120 14" fill="none" aria-hidden>
        {/* outer accent line */}
        <line x1="6"   y1="7" x2="46" y2="7"
              stroke="var(--accent)" strokeWidth="1" opacity="0.4" />
        <line x1="74"  y1="7" x2="114" y2="7"
              stroke="var(--accent)" strokeWidth="1" opacity="0.4" />
        {/* left + right diamond */}
        <rect x="2" y="3" width="8" height="8"
              transform="rotate(45 6 7)"
              fill="var(--accent)" opacity="0.6" />
        <rect x="110" y="3" width="8" height="8"
              transform="rotate(45 114 7)"
              fill="var(--accent)" opacity="0.6" />
        {/* central larger diamond with holo highlight */}
        <rect x="54" y="-1" width="14" height="14"
              transform="rotate(45 60 7)"
              fill="var(--holo)" opacity="0.85" />
        <rect x="56" y="1" width="10" height="10"
              transform="rotate(45 60 7)"
              fill="var(--accent)" />
      </svg>
    </div>
  )
}
