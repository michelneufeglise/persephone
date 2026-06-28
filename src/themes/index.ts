import type { Theme } from '@/types'

/**
 * Liquid Obsidian design system.
 *
 * Each theme carries:
 *   • A 4-stop background ramp (deeper than before) for parallax glass.
 *   • A 3-stop accent ramp (start / mid / end) used for gradient borders,
 *     button fills, and aurora washes.
 *   • A holographic edge colour for high-fidelity rim lighting.
 *   • A multi-stop shadow stack (key + ambient + glow) replacing flat shadows.
 */

export const themes: Theme[] = [
  {
    id: 'underworld',
    name: 'Underworld',
    description: 'Polished obsidian veined with pomegranate fire',
    preview: { bg: '#06040c', accent: '#d6356a', text: '#f5ecff' },
    vars: {
      // surface ramp — abyss → polished basalt
      '--bg-primary':    '#06040c',
      '--bg-secondary':  '#0c0918',
      '--bg-tertiary':   '#141027',
      '--bg-quaternary': '#1d1638',
      '--bg-glass':      'rgba(14,10,28,0.55)',
      '--bg-glass-strong':'rgba(20,16,42,0.78)',

      // borders
      '--border':        'rgba(214,53,106,0.18)',
      '--border-bright': 'rgba(214,53,106,0.55)',
      '--border-glass':  'rgba(255,255,255,0.06)',

      // accent ramp — pomegranate fire
      '--accent':        '#d6356a',
      '--accent-hover':  '#ef4d83',
      '--accent-mid':    '#7a1d52',
      '--accent-deep':   '#3a0a2c',
      '--accent-dim':    'rgba(214,53,106,0.14)',
      '--accent-glow':   'rgba(214,53,106,0.45)',

      // secondary holographic edge — cold ichor
      '--holo':          '#7df9ff',
      '--holo-dim':      'rgba(125,249,255,0.18)',

      // gold/saffron seam
      '--gold':          '#f0c060',
      '--gold-dim':      'rgba(240,192,96,0.18)',

      // text
      '--text-primary':  '#f5ecff',
      '--text-secondary':'#b9a4dc',
      '--text-muted':    '#675683',

      // orbs (avatar + hero gradients)
      '--orb-color-1':   '#d6356a',
      '--orb-color-2':   '#3a0a6b',
      '--orb-color-3':   '#7df9ff',

      // chat bubbles
      '--user-bubble':   'linear-gradient(135deg, rgba(214,53,106,0.22), rgba(58,10,44,0.55))',
      '--ai-bubble':     'linear-gradient(140deg, rgba(20,16,40,0.92), rgba(12,9,24,0.96))',
      '--thinking-bg':   'linear-gradient(135deg, rgba(125,249,255,0.06), rgba(74,15,107,0.18))',

      // shadow stack
      '--shadow-soft':   '0 1px 0 rgba(255,255,255,0.04) inset, 0 12px 32px -16px rgba(0,0,0,0.8)',
      '--shadow-deep':   '0 1px 0 rgba(255,255,255,0.05) inset, 0 30px 60px -28px rgba(0,0,0,0.9), 0 8px 24px -12px rgba(214,53,106,0.18)',
      '--shadow-glow':   '0 0 0 1px rgba(214,53,106,0.35), 0 0 40px -8px rgba(214,53,106,0.55), 0 0 80px -20px rgba(125,249,255,0.25)',

      '--scrollbar':     'rgba(214,53,106,0.35)',
    },
  },

  {
    id: 'spring',
    name: 'Spring Goddess',
    description: 'Iridescent dawn — when Persephone returns and the earth blooms',
    preview: { bg: '#f4eeff', accent: '#7c3aed', text: '#1a0a2e' },
    vars: {
      '--bg-primary':    '#f6f1ff',
      '--bg-secondary':  '#ece4fb',
      '--bg-tertiary':   '#e0d2f5',
      '--bg-quaternary': '#d1bff0',
      '--bg-glass':      'rgba(246,241,255,0.7)',
      '--bg-glass-strong':'rgba(255,255,255,0.85)',

      '--border':        'rgba(124,58,237,0.18)',
      '--border-bright': 'rgba(124,58,237,0.45)',
      '--border-glass':  'rgba(255,255,255,0.6)',

      '--accent':        '#7c3aed',
      '--accent-hover':  '#6d28d9',
      '--accent-mid':    '#a78bfa',
      '--accent-deep':   '#4c1d95',
      '--accent-dim':    'rgba(124,58,237,0.1)',
      '--accent-glow':   'rgba(124,58,237,0.35)',

      '--holo':          '#22d3ee',
      '--holo-dim':      'rgba(34,211,238,0.18)',

      '--gold':          '#d97706',
      '--gold-dim':      'rgba(217,119,6,0.15)',

      '--text-primary':  '#1a0a2e',
      '--text-secondary':'#5b4480',
      '--text-muted':    '#9684ba',

      '--orb-color-1':   '#7c3aed',
      '--orb-color-2':   '#22d3ee',
      '--orb-color-3':   '#d97706',

      '--user-bubble':   'linear-gradient(135deg, rgba(124,58,237,0.14), rgba(167,139,250,0.22))',
      '--ai-bubble':     'linear-gradient(140deg, rgba(255,255,255,0.95), rgba(224,210,245,0.85))',
      '--thinking-bg':   'linear-gradient(135deg, rgba(34,211,238,0.08), rgba(124,58,237,0.06))',

      '--shadow-soft':   '0 1px 0 rgba(255,255,255,0.9) inset, 0 12px 28px -18px rgba(124,58,237,0.35)',
      '--shadow-deep':   '0 1px 0 rgba(255,255,255,0.9) inset, 0 30px 60px -28px rgba(124,58,237,0.4), 0 6px 18px -10px rgba(34,211,238,0.25)',
      '--shadow-glow':   '0 0 0 1px rgba(124,58,237,0.3), 0 0 36px -8px rgba(124,58,237,0.4), 0 0 70px -20px rgba(34,211,238,0.35)',

      '--scrollbar':     'rgba(124,58,237,0.35)',
    },
  },

  {
    id: 'pomegranate',
    name: 'Pomegranate',
    description: 'Blood-red seeds set in lacquered black',
    preview: { bg: '#0a0306', accent: '#ff2e4e', text: '#ffe8eb' },
    vars: {
      '--bg-primary':    '#0a0306',
      '--bg-secondary':  '#16060b',
      '--bg-tertiary':   '#220912',
      '--bg-quaternary': '#330c19',
      '--bg-glass':      'rgba(22,6,11,0.6)',
      '--bg-glass-strong':'rgba(34,9,18,0.85)',

      '--border':        'rgba(255,46,78,0.18)',
      '--border-bright': 'rgba(255,46,78,0.55)',
      '--border-glass':  'rgba(255,255,255,0.05)',

      '--accent':        '#ff2e4e',
      '--accent-hover':  '#ff5a73',
      '--accent-mid':    '#a3081d',
      '--accent-deep':   '#52030d',
      '--accent-dim':    'rgba(255,46,78,0.14)',
      '--accent-glow':   'rgba(255,46,78,0.5)',

      '--holo':          '#ffd700',
      '--holo-dim':      'rgba(255,215,0,0.18)',

      '--gold':          '#ffd700',
      '--gold-dim':      'rgba(255,215,0,0.18)',

      '--text-primary':  '#ffe8eb',
      '--text-secondary':'#ef9aa6',
      '--text-muted':    '#8a3845',

      '--orb-color-1':   '#ff2e4e',
      '--orb-color-2':   '#52030d',
      '--orb-color-3':   '#ffd700',

      '--user-bubble':   'linear-gradient(135deg, rgba(255,46,78,0.22), rgba(82,3,13,0.65))',
      '--ai-bubble':     'linear-gradient(140deg, rgba(34,9,18,0.92), rgba(16,4,8,0.96))',
      '--thinking-bg':   'linear-gradient(135deg, rgba(255,215,0,0.06), rgba(82,3,13,0.2))',

      '--shadow-soft':   '0 1px 0 rgba(255,255,255,0.04) inset, 0 12px 32px -16px rgba(0,0,0,0.85)',
      '--shadow-deep':   '0 1px 0 rgba(255,255,255,0.05) inset, 0 30px 60px -28px rgba(0,0,0,0.95), 0 8px 24px -12px rgba(255,46,78,0.22)',
      '--shadow-glow':   '0 0 0 1px rgba(255,46,78,0.4), 0 0 40px -8px rgba(255,46,78,0.6), 0 0 80px -20px rgba(255,215,0,0.2)',

      '--scrollbar':     'rgba(255,46,78,0.35)',
    },
  },

  {
    id: 'elysian',
    name: 'Elysian Fields',
    description: 'Silver light over still water — paradise just beyond',
    preview: { bg: '#eef2f7', accent: '#3b82f6', text: '#0f172a' },
    vars: {
      '--bg-primary':    '#eef2f7',
      '--bg-secondary':  '#e0e7f0',
      '--bg-tertiary':   '#cfd8e6',
      '--bg-quaternary': '#bcc8da',
      '--bg-glass':      'rgba(238,242,247,0.7)',
      '--bg-glass-strong':'rgba(255,255,255,0.88)',

      '--border':        'rgba(59,130,246,0.18)',
      '--border-bright': 'rgba(59,130,246,0.5)',
      '--border-glass':  'rgba(255,255,255,0.7)',

      '--accent':        '#3b82f6',
      '--accent-hover':  '#2563eb',
      '--accent-mid':    '#60a5fa',
      '--accent-deep':   '#1e3a8a',
      '--accent-dim':    'rgba(59,130,246,0.1)',
      '--accent-glow':   'rgba(59,130,246,0.3)',

      '--holo':          '#06b6d4',
      '--holo-dim':      'rgba(6,182,212,0.18)',

      '--gold':          '#d97706',
      '--gold-dim':      'rgba(217,119,6,0.12)',

      '--text-primary':  '#0f172a',
      '--text-secondary':'#475569',
      '--text-muted':    '#8aa0bd',

      '--orb-color-1':   '#3b82f6',
      '--orb-color-2':   '#8b5cf6',
      '--orb-color-3':   '#06b6d4',

      '--user-bubble':   'linear-gradient(135deg, rgba(59,130,246,0.12), rgba(139,92,246,0.18))',
      '--ai-bubble':     'linear-gradient(140deg, rgba(255,255,255,0.95), rgba(207,216,230,0.88))',
      '--thinking-bg':   'linear-gradient(135deg, rgba(6,182,212,0.08), rgba(139,92,246,0.07))',

      '--shadow-soft':   '0 1px 0 rgba(255,255,255,0.95) inset, 0 12px 28px -18px rgba(59,130,246,0.35)',
      '--shadow-deep':   '0 1px 0 rgba(255,255,255,0.95) inset, 0 30px 60px -28px rgba(59,130,246,0.4), 0 6px 18px -10px rgba(6,182,212,0.22)',
      '--shadow-glow':   '0 0 0 1px rgba(59,130,246,0.3), 0 0 32px -8px rgba(59,130,246,0.4), 0 0 70px -20px rgba(6,182,212,0.3)',

      '--scrollbar':     'rgba(59,130,246,0.35)',
    },
  },

  {
    id: 'obsidian',
    name: 'Obsidian Garden',
    description: 'Midnight emerald — flowers grown in volcanic glass',
    preview: { bg: '#04090a', accent: '#34f0a2', text: '#d4fde8' },
    vars: {
      '--bg-primary':    '#04090a',
      '--bg-secondary':  '#08130f',
      '--bg-tertiary':   '#0f2018',
      '--bg-quaternary': '#163024',
      '--bg-glass':      'rgba(8,19,15,0.6)',
      '--bg-glass-strong':'rgba(15,32,24,0.82)',

      '--border':        'rgba(52,240,162,0.18)',
      '--border-bright': 'rgba(52,240,162,0.5)',
      '--border-glass':  'rgba(255,255,255,0.06)',

      '--accent':        '#34f0a2',
      '--accent-hover':  '#5cf7b8',
      '--accent-mid':    '#0e7a55',
      '--accent-deep':   '#053a2c',
      '--accent-dim':    'rgba(52,240,162,0.12)',
      '--accent-glow':   'rgba(52,240,162,0.4)',

      '--holo':          '#a78bfa',
      '--holo-dim':      'rgba(167,139,250,0.18)',

      '--gold':          '#fbbf24',
      '--gold-dim':      'rgba(251,191,36,0.18)',

      '--text-primary':  '#d4fde8',
      '--text-secondary':'#6ee7b7',
      '--text-muted':    '#34805b',

      '--orb-color-1':   '#34f0a2',
      '--orb-color-2':   '#053a2c',
      '--orb-color-3':   '#a78bfa',

      '--user-bubble':   'linear-gradient(135deg, rgba(52,240,162,0.18), rgba(5,58,44,0.55))',
      '--ai-bubble':     'linear-gradient(140deg, rgba(15,32,24,0.92), rgba(8,16,12,0.96))',
      '--thinking-bg':   'linear-gradient(135deg, rgba(167,139,250,0.08), rgba(14,122,85,0.16))',

      '--shadow-soft':   '0 1px 0 rgba(255,255,255,0.04) inset, 0 12px 32px -16px rgba(0,0,0,0.8)',
      '--shadow-deep':   '0 1px 0 rgba(255,255,255,0.05) inset, 0 30px 60px -28px rgba(0,0,0,0.9), 0 8px 24px -12px rgba(52,240,162,0.18)',
      '--shadow-glow':   '0 0 0 1px rgba(52,240,162,0.35), 0 0 40px -8px rgba(52,240,162,0.55), 0 0 80px -20px rgba(167,139,250,0.28)',

      '--scrollbar':     'rgba(52,240,162,0.35)',
    },
  },
]

export const defaultTheme = themes[0]

export function applyTheme(themeId: string) {
  const theme = themes.find(t => t.id === themeId) ?? defaultTheme
  const root = document.documentElement
  for (const [key, value] of Object.entries(theme.vars)) {
    root.style.setProperty(key, value)
  }
  root.setAttribute('data-theme', theme.id)
}
