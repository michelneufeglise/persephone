import { useMemo } from 'react'
import { motion } from 'framer-motion'
import {
  Type, Ruler, MoveHorizontal, ZoomIn, Wind, Contrast, Bold as BoldIcon,
  Underline, Focus, RotateCcw, Eye,
} from 'lucide-react'
import { clsx } from 'clsx'
import { Toggle } from '@/components/ui/Toggle'
import { Panel } from '@/components/ui/Panel'
import {
  useAppearance,
  FONT_SIZE_PRESETS, LINE_HEIGHT_PRESETS, LETTER_SPACING_PRESETS,
  FONT_FAMILY_PRESETS,
  type FontSizeKey, type LineHeightKey, type LetterSpacingKey,
  type FontFamilyKey, type MotionMode, type ContrastMode, type FocusRingStyle,
} from '@/lib/appearance'

// ── Config for the pickers ──────────────────────────────────────────────
const FONT_SIZE_OPTIONS: { id: FontSizeKey; label: string; sample: string; hint: string }[] = [
  { id: 'small',     label: 'Small',       sample: 'Aa', hint: '14 px — compact, fits more content' },
  { id: 'normal',    label: 'Normal',      sample: 'Aa', hint: '16 px — the default' },
  { id: 'large',     label: 'Large',       sample: 'Aa', hint: '18 px — a little easier on the eyes' },
  { id: 'x-large',   label: 'Extra Large', sample: 'Aa', hint: '20 px — comfortable for long reading' },
  { id: 'xx-large',  label: 'Huge',        sample: 'Aa', hint: '24 px — maximum legibility' },
]

const LINE_HEIGHT_OPTIONS: { id: LineHeightKey; label: string; hint: string }[] = [
  { id: 'compact', label: 'Compact', hint: 'Denser, more per screen' },
  { id: 'normal',  label: 'Normal',  hint: 'The default' },
  { id: 'relaxed', label: 'Relaxed', hint: 'Airier — recommended for long reading' },
]

const LETTER_SPACING_OPTIONS: { id: LetterSpacingKey; label: string; hint: string }[] = [
  { id: 'tight',  label: 'Tight',  hint: 'Slightly condensed' },
  { id: 'normal', label: 'Normal', hint: 'The default' },
  { id: 'wide',   label: 'Wide',   hint: 'More air between letters' },
]

const FONT_FAMILY_OPTIONS: { id: FontFamilyKey; label: string; hint: string }[] = [
  { id: 'default',  label: 'Designed pairing', hint: 'Manrope + Fraunces — the app\'s crafted look' },
  { id: 'system',   label: 'System UI',        hint: 'Your operating system font' },
  { id: 'serif',    label: 'Serif',            hint: 'Georgia — classic, book-like reading' },
  { id: 'mono',     label: 'Monospace',        hint: 'JetBrains Mono — for developers' },
  { id: 'dyslexic', label: 'Dyslexia-friendly', hint: 'Verdana + weighted forms — reduces letter confusion' },
]

const MOTION_OPTIONS: { id: MotionMode; label: string; hint: string }[] = [
  { id: 'full',    label: 'Full',    hint: 'All animations enabled' },
  { id: 'reduced', label: 'Reduced', hint: 'Snappy transitions, no drift or float' },
  { id: 'none',    label: 'None',    hint: 'No animation of any kind — best for vestibular sensitivity' },
]

const CONTRAST_OPTIONS: { id: ContrastMode; label: string; hint: string }[] = [
  { id: 'normal', label: 'Normal',        hint: 'The theme\'s designed colours' },
  { id: 'high',   label: 'High contrast', hint: 'Brighter text, thicker borders, muted backdrop' },
]

const FOCUS_RING_OPTIONS: { id: FocusRingStyle; label: string; hint: string }[] = [
  { id: 'subtle',    label: 'Subtle',    hint: 'Standard 2 px accent ring on keyboard focus' },
  { id: 'prominent', label: 'Prominent', hint: 'Thick 3 px ring — easier to see when tabbing through' },
  { id: 'hidden',    label: 'Hidden',    hint: 'No focus outline (not recommended for keyboard users)' },
]

// ── Section ─────────────────────────────────────────────────────────────
export function AppearanceSection() {
  const s = useAppearance()

  // Reset restores everything to defaults with a small confirm.
  function handleReset() {
    if (window.confirm('Reset every appearance setting to its default?')) {
      s.reset()
    }
  }

  const sizePx = FONT_SIZE_PRESETS[s.fontSize]

  return (
    <div className="max-w-3xl space-y-8 pb-12">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-serif text-2xl text-[var(--text-primary)] flex items-center gap-2">
            <Eye className="w-5 h-5 text-[var(--accent)]" /> Display &amp; accessibility
          </h3>
          <p className="text-sm text-[var(--text-muted)] mt-1 leading-relaxed">
            Make Persephone comfortable to read and use. Everything here is remembered
            per device and takes effect immediately.
          </p>
        </div>
        <button
          onClick={handleReset}
          className="flex-shrink-0 flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs text-[var(--text-muted)] border border-[var(--border)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors"
          title="Reset every appearance setting"
        >
          <RotateCcw className="w-3.5 h-3.5" /> Reset
        </button>
      </div>

      {/* Live preview */}
      <LivePreview />

      {/* Font size */}
      <Field icon={Type} label="Text size" hint="Sets the base body text size. Everything else scales with it.">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
          {FONT_SIZE_OPTIONS.map(opt => (
            <PickerCard
              key={opt.id}
              selected={s.fontSize === opt.id}
              onClick={() => s.set('fontSize', opt.id)}
              label={opt.label}
              hint={opt.hint}
              preview={
                <span style={{ fontSize: `${FONT_SIZE_PRESETS[opt.id]}px`, lineHeight: 1 }}>
                  {opt.sample}
                </span>
              }
            />
          ))}
        </div>
      </Field>

      {/* Line height */}
      <Field icon={Ruler} label="Line spacing" hint="Space between lines of text — bigger values are easier to read.">
        <div className="grid grid-cols-3 gap-2">
          {LINE_HEIGHT_OPTIONS.map(opt => (
            <PickerCard
              key={opt.id}
              selected={s.lineHeight === opt.id}
              onClick={() => s.set('lineHeight', opt.id)}
              label={opt.label}
              hint={opt.hint}
            />
          ))}
        </div>
      </Field>

      {/* Letter spacing */}
      <Field icon={MoveHorizontal} label="Letter spacing" hint="Space between characters. Tighter is denser; wider is more airy.">
        <div className="grid grid-cols-3 gap-2">
          {LETTER_SPACING_OPTIONS.map(opt => (
            <PickerCard
              key={opt.id}
              selected={s.letterSpacing === opt.id}
              onClick={() => s.set('letterSpacing', opt.id)}
              label={opt.label}
              hint={opt.hint}
              preview={
                <span style={{ letterSpacing: LETTER_SPACING_PRESETS[opt.id] }} className="text-sm">
                  Ag Sample
                </span>
              }
            />
          ))}
        </div>
      </Field>

      {/* Font family */}
      <Field icon={Type} label="Font family" hint="The typeface used throughout the app. Choose Dyslexia-friendly if you find letters hard to distinguish.">
        <div className="space-y-2">
          {FONT_FAMILY_OPTIONS.map(opt => (
            <button
              key={opt.id}
              onClick={() => s.set('fontFamily', opt.id)}
              className={clsx(
                'w-full text-left p-3 rounded-xl border transition-colors flex items-center justify-between gap-4',
                s.fontFamily === opt.id
                  ? 'border-[var(--accent)] bg-[var(--accent-dim)]'
                  : 'border-[var(--border)] bg-[var(--bg-tertiary)] hover:border-[var(--border-bright)]',
              )}
            >
              <div className="min-w-0">
                <div className="text-sm text-[var(--text-primary)] font-medium">{opt.label}</div>
                <div className="text-xs text-[var(--text-muted)] mt-0.5">{opt.hint}</div>
              </div>
              <div
                className="text-lg flex-shrink-0"
                style={{ fontFamily: FONT_FAMILY_PRESETS[opt.id].body }}
              >
                The quick brown fox
              </div>
            </button>
          ))}
        </div>
      </Field>

      {/* UI scale zoom */}
      <Field icon={ZoomIn} label="Overall zoom" hint="Scales the whole app — spacing, buttons, and images together.">
        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <span className="text-xs text-[var(--text-muted)] w-8">75%</span>
            <input
              type="range"
              min={75} max={150} step={5}
              value={Math.round(s.uiScale * 100)}
              onChange={e => s.set('uiScale', Number(e.target.value) / 100)}
              className="flex-1 accent-[var(--accent)]"
            />
            <span className="text-xs text-[var(--text-muted)] w-8 text-right">150%</span>
            <span className="text-sm font-mono text-[var(--accent)] w-14 text-right tabular-nums">
              {Math.round(s.uiScale * 100)}%
            </span>
          </div>
        </div>
      </Field>

      {/* Motion */}
      <Field icon={Wind} label="Motion" hint="How much the app animates. Set to Reduced or None if animations distract or make you dizzy.">
        <div className="grid grid-cols-3 gap-2">
          {MOTION_OPTIONS.map(opt => (
            <PickerCard
              key={opt.id}
              selected={s.motion === opt.id}
              onClick={() => s.set('motion', opt.id)}
              label={opt.label}
              hint={opt.hint}
            />
          ))}
        </div>
      </Field>

      {/* Contrast */}
      <Field icon={Contrast} label="Contrast" hint="High contrast brightens text and dims the atmospheric backdrop.">
        <div className="grid grid-cols-2 gap-2">
          {CONTRAST_OPTIONS.map(opt => (
            <PickerCard
              key={opt.id}
              selected={s.contrast === opt.id}
              onClick={() => s.set('contrast', opt.id)}
              label={opt.label}
              hint={opt.hint}
            />
          ))}
        </div>
      </Field>

      {/* Focus ring */}
      <Field icon={Focus} label="Focus outline" hint="The ring that appears around a button or input when you tab to it with the keyboard.">
        <div className="grid grid-cols-3 gap-2">
          {FOCUS_RING_OPTIONS.map(opt => (
            <PickerCard
              key={opt.id}
              selected={s.focusRing === opt.id}
              onClick={() => s.set('focusRing', opt.id)}
              label={opt.label}
              hint={opt.hint}
            />
          ))}
        </div>
      </Field>

      {/* Boolean switches */}
      <Panel className="p-4 space-y-4">
        <div className="flex items-center gap-3">
          <BoldIcon className="w-4 h-4 text-[var(--accent)] flex-shrink-0" />
          <Toggle
            checked={s.boldText}
            onChange={v => s.set('boldText', v)}
            label="Bold text everywhere"
            description="Make all text heavier so it stands out against the backdrop."
          />
        </div>
        <div className="flex items-center gap-3">
          <Underline className="w-4 h-4 text-[var(--accent)] flex-shrink-0" />
          <Toggle
            checked={s.underlineLinks}
            onChange={v => s.set('underlineLinks', v)}
            label="Underline all links"
            description="Adds an underline to every link so they're easy to spot without hovering."
          />
        </div>
      </Panel>

      {/* Current settings summary */}
      <div className="text-xs text-[var(--text-muted)] italic pt-2 border-t border-[var(--border)]">
        Current: {sizePx} px base · {(s.uiScale * 100).toFixed(0)}% zoom · {s.motion} motion · {s.contrast} contrast · {s.fontFamily} font
      </div>
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────
function Field({ icon: Icon, label, hint, children }: {
  icon: React.ElementType
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="flex items-start gap-2.5 mb-3">
        <Icon className="w-4 h-4 text-[var(--accent)] flex-shrink-0 mt-0.5" />
        <div className="min-w-0">
          <div className="text-sm font-medium text-[var(--text-primary)]">{label}</div>
          {hint && <div className="text-xs text-[var(--text-muted)] mt-0.5 leading-relaxed">{hint}</div>}
        </div>
      </div>
      {children}
    </div>
  )
}

function PickerCard({
  selected, onClick, label, hint, preview,
}: {
  selected: boolean
  onClick: () => void
  label: string
  hint?: string
  preview?: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'text-left p-3 rounded-xl border transition-colors flex flex-col gap-1',
        selected
          ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
          : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)] hover:text-[var(--text-primary)]',
      )}
    >
      {preview && (
        <div className="mb-1 h-8 flex items-center justify-start text-[var(--text-primary)]">
          {preview}
        </div>
      )}
      <div className="text-sm font-medium">{label}</div>
      {hint && (
        <div className={clsx('text-xs leading-relaxed', selected ? 'text-[var(--accent)]/80' : 'text-[var(--text-muted)]')}>
          {hint}
        </div>
      )}
    </button>
  )
}

// ── Live preview panel ─────────────────────────────────────────────────
function LivePreview() {
  // Sample content that exercises the tokens the user is changing. Wrapped
  // in a card that intentionally uses body font-size + line-height so the
  // effect is visible without leaving the settings page.
  const sample = useMemo(() => (
    <>
      <p>
        <strong>Preview:</strong> Persephone answers your prompts locally, using open-weight
        models on your machine. Try increasing the text size — this preview reflects your
        current choice.
      </p>
      <p style={{ marginTop: '0.75rem' }}>
        Links look like <a href="#preview" onClick={e => e.preventDefault()}>this one</a>, and
        keyboard focus rings appear on <button className="text-[var(--accent)] underline">buttons</button> as you tab through.
      </p>
    </>
  ), [])

  return (
    <motion.div layout
      className="rounded-2xl border border-[var(--border-bright)] bg-[var(--bg-glass-strong)] p-5 leading-relaxed text-[var(--text-primary)]"
      style={{ fontFamily: 'var(--font-family-body)' }}
    >
      {sample}
    </motion.div>
  )
}
