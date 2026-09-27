/**
 * Node components for the structured Network view and the Layers swimlanes.
 * One readable card for every run-graph kind, plus the background run panels
 * (Network), lane bands and the column header row (Layers).
 */
import React from 'react'
import { Handle, Position } from '@xyflow/react'
import { clsx } from 'clsx'
import {
  FileText,
  Image,
  Mail,
  File,
  Table2,
  Type,
  Layers,
  MessageCircle,
  Sparkles,
  Cpu,
  ListChecks,
  Globe,
  Database,
  ChevronsUpDown,
  Linkedin,
  Facebook,
  Instagram,
  Twitter,
} from 'lucide-react'
import { SOCIAL_PLATFORMS, resolvePlatform, type SocialPlatform } from './socialPlatforms'
import { visibleDecisionChips, type DecisionChip, type RunNodeKind } from './kgNetwork'

/** Accent colour per node kind (theme tokens where the theme has one). */
export const RUN_KIND_COLOR: Record<RunNodeKind, string> = {
  document: 'var(--text-secondary)',
  question: '#34d399',
  decisions: 'var(--gold)',
  model: 'var(--accent)',
  planner: '#60a5fa',
  web: 'var(--holo)',
  profile: 'var(--holo)',
  store: '#fb923c',
  runCompact: '#34d399',
}

export const RUN_KIND_LABEL: Record<RunNodeKind, string> = {
  document: 'Document',
  question: 'Question',
  decisions: 'Laya decisions',
  model: 'Model',
  planner: 'Planner',
  web: 'Web lookup',
  profile: 'Profile',
  store: 'Knowledge store',
  runCompact: 'Earlier run',
}

const DOC_ICONS: Record<string, React.ElementType> = {
  pdf: FileText,
  image: Image,
  email: Mail,
  docx: File,
  sheet: Table2,
  text: Type,
  other: Layers,
}

const PLATFORM_ICON: Record<SocialPlatform, React.ElementType> = {
  linkedin: Linkedin,
  facebook: Facebook,
  instagram: Instagram,
  x: Twitter,
}

const CHIP_TONE_COLOR: Record<DecisionChip['tone'], string> = {
  intent: 'var(--gold)',
  web: 'var(--holo)',
  role: 'var(--text-secondary)',
  model: 'var(--accent)',
  tool: '#fb923c',
  doc: 'var(--text-secondary)',
  other: 'var(--text-muted)',
}

const HIDDEN_HANDLE: React.CSSProperties = { opacity: 0, pointerEvents: 'none', width: 1, height: 1, minWidth: 0, minHeight: 0, border: 0 }

function str(v: unknown): string {
  return typeof v === 'string' ? v : typeof v === 'number' ? String(v) : ''
}

function iconFor(kind: RunNodeKind, d: Record<string, unknown>): { Icon: React.ElementType; color: string } {
  switch (kind) {
    case 'document':
      return { Icon: DOC_ICONS[str(d.kind)] || DOC_ICONS.other, color: RUN_KIND_COLOR.document }
    case 'question':
      return { Icon: MessageCircle, color: RUN_KIND_COLOR.question }
    case 'runCompact':
      return { Icon: ChevronsUpDown, color: RUN_KIND_COLOR.runCompact }
    case 'decisions':
      return { Icon: Sparkles, color: RUN_KIND_COLOR.decisions }
    case 'model':
      return { Icon: d.isLaya ? Sparkles : Cpu, color: d.isLaya ? 'var(--gold)' : RUN_KIND_COLOR.model }
    case 'planner':
      return { Icon: ListChecks, color: RUN_KIND_COLOR.planner }
    case 'web':
      return { Icon: Globe, color: RUN_KIND_COLOR.web }
    case 'profile': {
      const platform = resolvePlatform(d.platform, str(d.url) || str(d.host))
      return platform
        ? { Icon: PLATFORM_ICON[platform], color: SOCIAL_PLATFORMS[platform].color }
        : { Icon: Globe, color: RUN_KIND_COLOR.profile }
    }
    case 'store':
      return { Icon: Database, color: RUN_KIND_COLOR.store }
  }
}

function statusTone(status: string): string | null {
  if (status === 'error') return 'rgb(239 68 68)'
  if (status === 'running' || status === 'streaming' || status === 'pending') return 'var(--accent)'
  if (status === 'cancelled') return '#eab308'
  return null
}

/** One-line secondary text for a card. */
function subtitleFor(kind: RunNodeKind, d: Record<string, unknown>): string {
  switch (kind) {
    case 'document': {
      const roles = Array.isArray(d.roles) ? (d.roles as string[]) : []
      return [str(d.kind) !== 'other' ? str(d.kind).toUpperCase() : '', roles.join(', ')].filter(Boolean).join(' · ')
    }
    case 'question': {
      const a = (d.answer || null) as { model?: string | null; ms?: number | null } | null
      return a ? [a.model || '', typeof a.ms === 'number' ? `${(a.ms / 1000).toFixed(1)}s` : ''].filter(Boolean).join(' · ') : 'waiting for answer…'
    }
    case 'runCompact':
      return [str(d.intent), str(d.answerModel)].filter(Boolean).join(' · ') || 'click to expand'
    case 'model': {
      const roles = Array.isArray(d.roles) ? (d.roles as string[]) : []
      return roles.join(' · ')
    }
    case 'planner':
    case 'web':
    case 'store':
      return str(d.detail)
    case 'profile':
      return str(d.host)
    default:
      return ''
  }
}

export interface RunCardData {
  kind: RunNodeKind
  label: string
  data: Record<string, unknown>
  variant: 'network' | 'lanes'
  isRef?: boolean
  selected?: boolean
  focused?: boolean
  match?: boolean
  [key: string]: unknown
}

function CardHandles() {
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} style={HIDDEN_HANDLE} />
      <Handle type="source" position={Position.Right} isConnectable={false} style={HIDDEN_HANDLE} />
    </>
  )
}

/** Card for every run-graph node kind (sized by the node's style width/height). */
export function RunCardNode({ data }: { data: RunCardData }) {
  const d = (data?.data || {}) as Record<string, unknown>
  const kind = data?.kind || 'document'
  const { Icon, color } = iconFor(kind, d)
  const lanes = data.variant === 'lanes'
  const selected = !!data.selected
  const ring = selected
    ? `0 0 0 1.5px ${color}, 0 10px 28px -14px ${color}`
    : data.match
      ? `0 0 0 1.5px var(--accent)`
      : data.focused
        ? `0 0 0 1px color-mix(in oklab, ${color} 60%, transparent)`
        : undefined

  // Lane reference chip: a lightweight stand-in for a shared document / model.
  if (data.isRef) {
    return (
      <>
        <CardHandles />
        <div
          className="w-full h-full rounded-xl flex items-center gap-2 px-2.5 overflow-hidden"
          style={{
            background: `color-mix(in oklab, ${color} 7%, var(--bg-glass))`,
            border: `1px dashed color-mix(in oklab, ${color} 45%, transparent)`,
            boxShadow: ring,
          }}
          title={data.label}
        >
          <Icon className="w-3.5 h-3.5 flex-shrink-0" style={{ color }} />
          <div className="min-w-0 flex-1">
            <div className="text-[12px] font-semibold text-[var(--text-primary)] truncate leading-tight">{data.label}</div>
            <div className="text-[9.5px] uppercase tracking-[0.1em] text-[var(--text-muted)] truncate">
              {RUN_KIND_LABEL[kind]}
              {kind === 'model' && Number(d.useCount) > 1 ? ` · ${d.useCount} runs` : ''}
            </div>
          </div>
        </div>
      </>
    )
  }

  if (kind === 'decisions') {
    const chips = (Array.isArray(d.chips) ? d.chips : []) as DecisionChip[]
    const texts = visibleDecisionChips(chips)
    return (
      <>
        <CardHandles />
        <div
          className="glass-card w-full h-full rounded-2xl px-3 py-2 flex flex-col gap-1.5 overflow-hidden"
          style={{
            background: `color-mix(in oklab, ${color} 11%, transparent)`,
            borderColor: `color-mix(in oklab, ${color} 50%, transparent)`,
            borderWidth: 1,
            boxShadow: ring,
          }}
        >
          <div className="flex items-center gap-1.5 min-w-0">
            <Sparkles className="w-3.5 h-3.5 flex-shrink-0" style={{ color }} />
            <span className="text-[9.5px] font-bold uppercase tracking-[0.12em] truncate" style={{ color }}>
              Laya decisions
            </span>
            {typeof d.ms === 'number' && (
              <span className="ml-auto text-[10px] font-mono text-[var(--text-muted)]">{((d.ms as number) / 1000).toFixed(1)}s</span>
            )}
          </div>
          <div className="flex flex-wrap gap-1">
            {texts.map((t, i) => {
              const tone = chips[i]?.tone || 'other'
              const c = CHIP_TONE_COLOR[tone]
              return (
                <span
                  key={`${t}-${i}`}
                  className="max-w-full truncate rounded-full px-2 text-[10.5px] leading-5 text-[var(--text-secondary)]"
                  style={{ background: `color-mix(in oklab, ${c} 14%, transparent)`, border: `1px solid color-mix(in oklab, ${c} 32%, transparent)` }}
                  title={t}
                >
                  {t}
                </span>
              )
            })}
          </div>
        </div>
      </>
    )
  }

  const subtitle = subtitleFor(kind, d)
  const status = str(d.status) || str((d.answer as Record<string, unknown> | null)?.status)
  const statusColor = statusTone(status)
  const badge =
    kind === 'model' && Number(d.useCount) > 1
      ? `×${d.useCount}`
      : kind === 'document' && Array.isArray(d.runKeys) && (d.runKeys as string[]).length > 1
        ? `×${(d.runKeys as string[]).length}`
        : ''

  return (
    <>
      <CardHandles />
      <div
        className={clsx(
          'glass-card w-full h-full rounded-2xl flex items-center gap-2.5 overflow-hidden',
          lanes ? 'px-3 py-2' : 'px-2.5 py-2',
        )}
        style={{
          background: `color-mix(in oklab, ${color} ${kind === 'question' ? 10 : 7}%, transparent)`,
          borderColor: `color-mix(in oklab, ${color} ${selected ? 80 : 40}%, transparent)`,
          borderWidth: 1,
          borderStyle: kind === 'runCompact' ? 'dashed' : 'solid',
          boxShadow: ring,
        }}
        title={str(d.fullText) || str(d.fullLabel) || data.label}
      >
        <span
          className="flex-shrink-0 w-7 h-7 rounded-lg flex items-center justify-center"
          style={{ background: `color-mix(in oklab, ${color} 18%, transparent)`, color }}
        >
          <Icon className="w-3.5 h-3.5" />
        </span>
        <div className="min-w-0 flex-1 flex flex-col gap-0.5">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="text-[9px] font-bold uppercase tracking-[0.12em] truncate" style={{ color }}>
              {kind === 'model' && d.isLaya ? 'Decision model' : RUN_KIND_LABEL[kind]}
            </span>
            {statusColor && <span className="flex-shrink-0 w-1.5 h-1.5 rounded-full" style={{ background: statusColor }} title={status} />}
            {badge && <span className="ml-auto flex-shrink-0 text-[10px] font-mono text-[var(--text-muted)]">{badge}</span>}
          </div>
          <div
            className={clsx(
              'font-semibold text-[var(--text-primary)] leading-snug line-clamp-2 break-words',
              lanes ? 'text-[13px]' : 'text-[12.5px]',
              kind === 'model' && 'font-mono !text-[12px]',
              (kind === 'question' || kind === 'runCompact') && '!font-medium',
            )}
          >
            {data.label}
          </div>
          {subtitle && kind !== 'question' && kind !== 'runCompact' ? (
            <div className="text-[10.5px] text-[var(--text-muted)] truncate leading-tight" title={subtitle}>
              {subtitle}
            </div>
          ) : null}
        </div>
      </div>
    </>
  )
}

// ── Network: run panels ─────────────────────────────────────────────────────

export interface RunBandData {
  parts: { x: number; y: number; width: number; height: number; role: 'main' | 'results' }[]
  label: string
  time: string
  status: string
  active: boolean
  dim: boolean
  onSelect?: () => void
  [key: string]: unknown
}

/** Subtle background panel behind one run (main part + results part). */
export function RunBandNode({ data }: { data: RunBandData }) {
  const statusColor = statusTone(data.status)
  return (
    <div className="relative w-full h-full" style={{ opacity: data.dim ? 0.35 : 1, transition: 'opacity 200ms ease' }}>
      {data.parts.map((p, i) => (
        <div
          key={i}
          className={clsx('kg-run-band absolute rounded-[20px]', data.active && 'is-active')}
          style={{ left: p.x, top: p.y, width: p.width, height: p.height }}
        >
          {p.role === 'main' ? (
            <button
              type="button"
              onClick={e => {
                e.stopPropagation()
                data.onSelect?.()
              }}
              className="kg-run-band__label absolute left-3 top-1 right-3 flex items-center gap-1.5 text-left"
              style={{ pointerEvents: 'auto' }}
              title={`${data.label} — select this run`}
            >
              {statusColor && <span className="flex-shrink-0 w-1.5 h-1.5 rounded-full" style={{ background: statusColor }} />}
              <span className="truncate text-[10.5px] font-semibold">{data.label}</span>
              {data.time && <span className="flex-shrink-0 text-[10px] font-mono opacity-70">{data.time}</span>}
            </button>
          ) : (
            <span className="kg-run-band__label absolute left-3 top-1 text-[9.5px] uppercase tracking-[0.12em] font-semibold">results</span>
          )}
        </div>
      ))}
    </div>
  )
}

// ── Layers: lanes + column header ────────────────────────────────────────────

export interface LaneBandData {
  label: string
  time: string
  status: string
  index: number
  collapsed: boolean
  labelWidth: number
  active: boolean
  dim: boolean
  onSelect?: () => void
  [key: string]: unknown
}

/** One swimlane: rounded band with the run's question (+ time) on the left. */
export function LaneBandNode({ data }: { data: LaneBandData }) {
  const statusColor = statusTone(data.status)
  return (
    <div
      className={clsx('kg-lane w-full h-full rounded-[22px] relative', data.active && 'is-active')}
      style={{ opacity: data.dim ? 0.45 : 1, transition: 'opacity 200ms ease' }}
    >
      <button
        type="button"
        onClick={e => {
          e.stopPropagation()
          data.onSelect?.()
        }}
        className="kg-lane__label absolute left-0 top-0 bottom-0 flex flex-col justify-center gap-1 px-4 text-left rounded-l-[22px]"
        style={{ width: data.labelWidth, pointerEvents: 'auto' }}
        title={`${data.label} — select this run`}
      >
        <span className="flex items-center gap-1.5 text-[9.5px] uppercase tracking-[0.12em] font-bold text-[var(--text-muted)]">
          {statusColor && <span className="w-1.5 h-1.5 rounded-full" style={{ background: statusColor }} />}
          Run {data.index + 1}
          {data.time && <span className="font-mono normal-case tracking-normal font-medium">· {data.time}</span>}
        </span>
        <span className="text-[12.5px] font-semibold leading-snug text-[var(--text-primary)] line-clamp-3 break-words">{data.label}</span>
        {data.collapsed && <span className="text-[10px] text-[var(--text-muted)]">collapsed · click the card to expand</span>}
      </button>
    </div>
  )
}

export interface LaneHeaderData {
  columns: { x: number; width: number; title: string }[]
  labelWidth: number
  [key: string]: unknown
}

/** Column titles above the lanes. */
export function LaneHeaderNode({ data }: { data: LaneHeaderData }) {
  return (
    <div className="relative w-full h-full pointer-events-none">
      <div
        className="absolute top-0 bottom-0 flex items-center px-4 text-[10px] uppercase tracking-[0.14em] font-bold text-[var(--text-muted)]"
        style={{ left: 0, width: data.labelWidth }}
      >
        Run
      </div>
      {data.columns.map(c => (
        <div
          key={c.title}
          className="absolute top-0 bottom-0 flex items-center justify-center text-[10px] uppercase tracking-[0.14em] font-bold text-[var(--text-muted)] border-b border-[var(--glass-stroke)]"
          style={{ left: c.x, width: c.width }}
        >
          {c.title}
        </div>
      ))}
    </div>
  )
}
