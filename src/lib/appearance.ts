/**
 * Persephone appearance / accessibility preferences.
 *
 * Self-contained: does not touch the main appStore. State is persisted in
 * localStorage under `persephone-appearance`. A single `useAppearanceApply`
 * hook keeps the CSS custom properties + data-attributes on <html> in sync
 * with the current settings — mount it once in App.tsx.
 *
 * Accessibility-first defaults: everything ships at the level the app has
 * had for months, so returning users see no visible change until they open
 * Settings → Display.
 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { useEffect } from 'react'

// ── Preset scales ──────────────────────────────────────────────────────
export const FONT_SIZE_PRESETS = {
  small:      14,
  normal:     16,
  large:      18,
  'x-large':  20,
  'xx-large': 24,
} as const

export const LINE_HEIGHT_PRESETS = {
  compact: 1.35,
  normal:  1.55,
  relaxed: 1.8,
} as const

export const LETTER_SPACING_PRESETS = {
  tight:  '-0.02em',
  normal: '-0.005em',
  wide:   '0.03em',
} as const

// Font families. `default` = the site's designed pairing (Manrope + Fraunces).
// `system` uses the OS UI font. `serif` is a comfortable serif for long-form
// reading. `mono` for developers. `dyslexic` opts into a dyslexia-friendly
// stack (Comic Sans MS is the most widely bundled option shown to help).
export const FONT_FAMILY_PRESETS = {
  default:  {
    body:    "'Manrope', system-ui, sans-serif",
    display: "'Fraunces', serif",
  },
  system:   {
    body:    'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    display: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  },
  serif:    {
    body:    'Georgia, "Times New Roman", serif',
    display: 'Georgia, "Times New Roman", serif',
  },
  mono:     {
    body:    "'JetBrains Mono', ui-monospace, Menlo, monospace",
    display: "'JetBrains Mono', ui-monospace, Menlo, monospace",
  },
  dyslexic: {
    // Comic Sans MS ships on macOS, Windows, most Linux distros with a
    // liberation-fonts pack. It's not perfect but it's universally available
    // and the shapes match OpenDyslexic's principles (weighted bottoms,
    // distinct letterforms).
    body:    'Verdana, "Comic Sans MS", "Trebuchet MS", sans-serif',
    display: 'Verdana, "Comic Sans MS", "Trebuchet MS", sans-serif',
  },
} as const

export type FontSizeKey      = keyof typeof FONT_SIZE_PRESETS
export type LineHeightKey    = keyof typeof LINE_HEIGHT_PRESETS
export type LetterSpacingKey = keyof typeof LETTER_SPACING_PRESETS
export type FontFamilyKey    = keyof typeof FONT_FAMILY_PRESETS

export type MotionMode     = 'full' | 'reduced' | 'none'
export type ContrastMode   = 'normal' | 'high'
export type FocusRingStyle = 'subtle' | 'prominent' | 'hidden'

export interface AppearanceSettings {
  fontSize:       FontSizeKey
  lineHeight:     LineHeightKey
  letterSpacing:  LetterSpacingKey
  fontFamily:     FontFamilyKey
  uiScale:        number           // 0.75 – 1.5, in 0.05 steps
  motion:         MotionMode
  contrast:       ContrastMode
  boldText:       boolean
  underlineLinks: boolean
  focusRing:      FocusRingStyle
}

export const APPEARANCE_DEFAULTS: AppearanceSettings = {
  fontSize:       'normal',
  lineHeight:     'normal',
  letterSpacing:  'normal',
  fontFamily:     'default',
  uiScale:        1.0,
  motion:         'full',
  contrast:       'normal',
  boldText:       false,
  underlineLinks: false,
  focusRing:      'subtle',
}

interface Store extends AppearanceSettings {
  set: <K extends keyof AppearanceSettings>(k: K, v: AppearanceSettings[K]) => void
  reset: () => void
}

export const useAppearance = create<Store>()(
  persist(
    (set) => ({
      ...APPEARANCE_DEFAULTS,
      set: (k, v) => set({ [k]: v } as Partial<Store>),
      reset: () => set({ ...APPEARANCE_DEFAULTS }),
    }),
    { name: 'persephone-appearance' },
  ),
)

/**
 * Mount once (in App.tsx). Watches the appearance store and mirrors the
 * settings onto the <html> element so global CSS in index.css can react.
 */
export function useAppearanceApply(): void {
  const s = useAppearance()

  useEffect(() => {
    const root = document.documentElement
    const setVar = (k: string, v: string | number) => root.style.setProperty(k, String(v))

    // Numeric / string CSS variables
    setVar('--font-size-base',      `${FONT_SIZE_PRESETS[s.fontSize]}px`)
    setVar('--line-height-base',    LINE_HEIGHT_PRESETS[s.lineHeight])
    setVar('--letter-spacing-base', LETTER_SPACING_PRESETS[s.letterSpacing])
    setVar('--font-family-body',    FONT_FAMILY_PRESETS[s.fontFamily].body)
    setVar('--font-family-display', FONT_FAMILY_PRESETS[s.fontFamily].display)
    setVar('--ui-scale',            s.uiScale)

    // Boolean / enum data-attributes
    root.dataset.motion         = s.motion
    root.dataset.contrast       = s.contrast
    root.dataset.boldText       = s.boldText ? 'true' : 'false'
    root.dataset.underlineLinks = s.underlineLinks ? 'true' : 'false'
    root.dataset.focusRing      = s.focusRing
  }, [
    s.fontSize, s.lineHeight, s.letterSpacing, s.fontFamily,
    s.uiScale, s.motion, s.contrast, s.boldText,
    s.underlineLinks, s.focusRing,
  ])
}
