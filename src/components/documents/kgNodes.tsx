import React, { useMemo } from 'react'
import { FileText, Image, Mail, File, Table2, Type, Layers, FileJson, MessageCircle, Sparkles, Brain, Cpu, ListChecks, Globe, User, Check, Building2, Briefcase, Linkedin, Facebook, Instagram, Twitter, MapPin } from 'lucide-react'
import { clsx } from 'clsx'
import { Handle, Position } from '@xyflow/react'
import { NODE_SIZES, entityNodeSize } from './kgModel'
import { SOCIAL_PLATFORMS, resolvePlatform } from './socialPlatforms'
import type { SocialPlatform } from './socialPlatforms'

/** Lucide icon per social platform (profiles on other sites use Globe). */
const PLATFORM_ICON: Record<SocialPlatform, React.ElementType> = {
  linkedin: Linkedin,
  facebook: Facebook,
  instagram: Instagram,
  x: Twitter,
}

/** Platform, colour and icon for a profile node (data.platform, else the URL/host). */
function profileVisual(data: Record<string, unknown>): { platform: SocialPlatform | null; color: string; icon: React.ElementType; label: string } {
  const props = (data.props && typeof data.props === 'object' ? data.props : {}) as Record<string, unknown>
  const url = typeof data.url === 'string' ? data.url : typeof props.url === 'string' ? props.url : ''
  const host = typeof data.host === 'string' ? data.host : ''
  const platform = resolvePlatform(data.platform, url || host)
  if (!platform) return { platform: null, color: '#0a66c2', icon: Globe, label: 'Web' }
  const p = SOCIAL_PLATFORMS[platform]
  return { platform, color: p.color, icon: PLATFORM_ICON[platform], label: p.label }
}

/**
 * Custom node components for the knowledge graph with premium styling
 */

interface NodeProps {
  data: any
  selected?: boolean
}

export function DocumentNodeComponent({ data, selected }: NodeProps) {
  const docKindIcons: Record<string, React.ElementType> = {
    pdf: FileText,
    image: Image,
    email: Mail,
    docx: File,
    sheet: Table2,
    text: Type,
    other: Layers,
  }

  const kind = (data.kind as string) || 'other'
  const IconComponent = docKindIcons[kind] || docKindIcons.other
  const filename = data.filename || data.label || 'Document'
  const isHighlighted = data.highlighted === true

  const { width, height } = NODE_SIZES.document

  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        style={{ width, height, overflow: 'hidden' }}
        className={clsx(
          'flex items-center gap-2 px-2.5 py-1.5',
          'rounded-[12px] border',
          'transition-all duration-200 hover:shadow-[0_8px_16px_var(--shadow-glow)]',
          selected
            ? 'border-[var(--accent)] bg-gradient-to-br from-[var(--accent-dim)] to-[var(--bg-tertiary)] shadow-[0_0_12px_var(--accent-glow)]'
            : isHighlighted
              ? 'border-[var(--border-glass)] bg-gradient-to-br from-[var(--accent-dim)]/10 to-[var(--bg-tertiary)] shadow-[var(--shadow-soft)]'
              : 'border-[var(--border-glass)] bg-[var(--bg-glass)] shadow-[var(--shadow-soft)] opacity-25',
        )}
      >
        <div className="w-7 h-7 flex-shrink-0 rounded-lg flex items-center justify-center bg-[var(--accent-dim)]">
          <IconComponent className="w-4 h-4 text-[var(--accent)]" />
        </div>
        <div className="min-w-0 flex-1">
          <div
            className="text-[0.7rem] font-semibold text-[var(--text-primary)] leading-tight truncate"
            title={filename}
          >
            {filename}
          </div>
          {kind && kind !== 'other' && (
            <span className="inline-block mt-0.5 text-[0.55rem] uppercase tracking-wider font-medium text-[var(--text-muted)]  px-1.5 py-px rounded-full">
              {kind}
            </span>
          )}
        </div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function QuestionNodeComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true

  const statusDot = useMemo(() => {
    if (!data.answer) return null
    const status = data.answer.status || 'pending'
    const colors = {
      done: 'animate-pulse bg-green-500',
      error: 'bg-red-500',
      cancelled: 'bg-yellow-500',
      pending: 'animate-pulse bg-[var(--accent)]',
    }
    return colors[status as keyof typeof colors] || colors.pending
  }, [data.answer])

  const { width, height } = NODE_SIZES.question

  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        style={{ width, height, overflow: 'hidden' }}
        className={clsx(
          'flex flex-col gap-1 px-2 py-1.5',
          'rounded-[12px] border backdrop-blur-sm',
          'transition-all duration-200 hover:shadow-[0_8px_16px_var(--shadow-glow)]',
          selected
            ? 'border-[var(--accent)] bg-gradient-to-br from-[var(--accent-dim)] to-[var(--bg-tertiary)] shadow-[0_0_12px_var(--accent-glow)]'
            : isHighlighted
              ? 'border-[var(--border-glass)] bg-gradient-to-br from-[var(--accent-dim)]/15 to-[var(--bg-tertiary)] shadow-[var(--shadow-soft)]'
              : 'border-[var(--border-glass)] bg-[var(--bg-glass)] shadow-[var(--shadow-soft)] opacity-25',
        )}
      >
        <div className="flex items-start gap-1.5">
          <MessageCircle className="w-3 h-3 flex-shrink-0 mt-0.5 text-[var(--accent)]" />
          <div className="text-[0.65rem] leading-tight line-clamp-2 flex-1 text-[var(--text-primary)] font-medium">
            {data.shortLabel || data.label}
          </div>
          {statusDot && (
            <div className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0 mt-0.5', statusDot)} />
          )}
        </div>
        {data.answer && (
          <div className="text-[0.6rem] text-[var(--text-muted)] truncate font-mono">{data.answer.model || '—'}</div>
        )}
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function DecisionNodeComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true

  const sourceColors: Record<string, { border: string; bg: string; shadow: string }> = {
    laya: {
      border: 'border-[var(--accent)]/60',
      bg: 'bg-gradient-to-br from-[var(--accent)]/25 to-[var(--accent-deep)]/10',
      shadow: 'shadow-[0_0_8px_var(--accent-glow)]',
    },
    rules: {
      border: 'border-[var(--accent-mid)]/60',
      bg: 'bg-gradient-to-br from-[var(--accent-mid)]/25 to-[var(--accent-mid)]/10',
      shadow: 'shadow-[0_0_8px_var(--accent-glow)]/30',
    },
    probe: {
      border: 'border-[var(--accent-dim)]/60',
      bg: 'bg-gradient-to-br from-[var(--accent-dim)]/25 to-[var(--accent-dim)]/10',
      shadow: 'shadow-[0_0_8px_var(--accent-glow)]/15',
    },
    config: {
      border: 'border-[var(--border-glass)]',
      bg: 'bg-gradient-to-br from-[var(--bg-secondary)]/15 to-[var(--bg-secondary)]/8',
      shadow: 'shadow-[0_0_8px_var(--border-glass)]',
    },
    user: {
      border: 'border-[var(--accent-mid)]/60',
      bg: 'bg-gradient-to-br from-[var(--accent-mid)]/25 to-[var(--accent)]/10',
      shadow: 'shadow-[0_0_8px_var(--accent-glow)]/20',
    },
  }
  const colorSet = sourceColors[data.source as keyof typeof sourceColors] || sourceColors.config

  const sourceLabels = {
    laya: 'Laya',
    rules: 'Rules',
    probe: 'Probe',
    config: 'Config',
    user: 'You',
  }

  const { width, height } = NODE_SIZES.decision

  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        style={{ width, height, overflow: 'hidden' }}
        className={clsx(
          'flex flex-col gap-1 px-2 py-1.5 justify-center',
          'rounded-[10px] border',
          'transition-all duration-200 hover:shadow-[0_8px_16px_var(--shadow-glow)]',
          selected ? 'ring-2 ring-[var(--accent)] ring-offset-1 ring-offset-[var(--bg-primary)]' : '',
          isHighlighted
            ? clsx(colorSet.border, colorSet.bg, colorSet.shadow, 'border-opacity-100')
            : clsx(colorSet.border, colorSet.bg, 'border-opacity-60 bg-opacity-40 opacity-25'),
        )}
      >
        <div className="flex items-center gap-1">
          <div className="text-[0.5rem] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-full  text-[var(--text-muted)]">
            {sourceLabels[data.source as keyof typeof sourceLabels] || data.source}
          </div>
        </div>
        <div className="text-[0.65rem] font-semibold text-[var(--text-primary)] line-clamp-2 leading-tight">
          {data.label}
        </div>
        {data.confidence !== null && data.confidence !== undefined && (
          <div className="w-full h-1  rounded-full overflow-hidden mt-0.5">
            <div
              className="h-full bg-gradient-to-r from-[var(--accent)] to-[var(--accent-deep)] transition-all duration-300"
              style={{ width: `${Math.min(100, Math.max(0, data.confidence * 100))}%` }}
            />
          </div>
        )}
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function ModelNodeComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true
  const isLaya = data.modelName === 'Laya'
  const modelName = data.modelName || data.label || 'Model'
  const displayRoles = (data.roles || []).slice(0, 2) as string[]
  const moreRoles = Math.max(0, (data.roles || []).length - 2)

  const { width, height } = NODE_SIZES.model

  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        style={{ width, height, overflow: 'hidden' }}
        className={clsx(
          'flex items-center gap-2 px-2 py-1.5',
          'rounded-[12px] border',
          'transition-all duration-200 hover:shadow-[0_8px_16px_var(--shadow-glow)]',
          isLaya && !selected && isHighlighted
            ? 'border-[var(--accent)] bg-gradient-to-br from-[var(--accent-dim)] to-[var(--bg-tertiary)] shadow-[0_0_16px_var(--accent-glow)]'
            : selected
              ? 'border-[var(--accent)] bg-gradient-to-br from-[var(--accent-dim)] to-[var(--bg-tertiary)] shadow-[0_0_12px_var(--accent-glow)]'
              : isHighlighted
                ? 'border-[var(--border-glass)] bg-gradient-to-br from-[var(--accent-dim)]/15 to-[var(--bg-tertiary)] shadow-[var(--shadow-soft)]'
                : 'border-[var(--border-glass)] bg-[var(--bg-glass)] shadow-[var(--shadow-soft)] opacity-25',
        )}
      >
        {/* Icon avatar: 28px circular */}
        <div
          className={clsx(
            'w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0',
            'bg-gradient-to-br shadow-md',
            isLaya
              ? 'from-[var(--accent)] to-[var(--accent-deep)] shadow-[0_0_12px_var(--accent-glow)]'
              : (data.useCount || 0) > 5
                ? 'from-[var(--accent)] to-[var(--accent-mid)]'
                : 'from-[var(--accent-dim)] to-[var(--bg-tertiary)]',
          )}
        >
          {isLaya ? (
            <Sparkles className="w-4 h-4 text-white" />
          ) : (
            <Cpu className="w-4 h-4 text-white" />
          )}
        </div>

        {/* Middle section: name + roles row */}
        <div className="flex-1 min-w-0 flex flex-col gap-0.5">
          {/* Model name */}
          <div
            className="text-[0.65rem] font-mono font-semibold text-[var(--text-primary)] truncate"
            title={modelName}
          >
            {modelName}
          </div>

          {/* Roles and use count in one row */}
          <div className="flex items-center gap-1 min-w-0">
            {displayRoles.map(r => (
              <span
                key={r}
                className="px-1 py-0 rounded-full text-[0.45rem] uppercase font-bold tracking-wider bg-[var(--accent-dim)] text-[var(--accent)] whitespace-nowrap"
              >
                {r}
              </span>
            ))}
            {moreRoles > 0 && (
              <span className="text-[0.45rem] text-[var(--text-muted)] font-semibold">
                +{moreRoles}
              </span>
            )}
          </div>
        </div>

        {/* Use count at the end */}
        {Number(data.useCount) > 1 && (
          <div className="text-[0.55rem] text-[var(--text-muted)] font-mono flex-shrink-0">
            ×{data.useCount}
          </div>
        )}
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function PlannerNodeComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true

  const { width, height } = NODE_SIZES.planner

  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        style={{ width, height, overflow: 'hidden' }}
        className={clsx(
          'flex items-center gap-2 px-2.5 py-1.5',
          'rounded-[12px] border',
          'transition-all duration-200 hover:shadow-[0_8px_16px_var(--shadow-glow)]',
          selected
            ? 'border-[var(--gold)] bg-gradient-to-br from-[var(--gold)]/20 to-[var(--bg-tertiary)] shadow-[0_0_12px_var(--gold)]/30'
            : isHighlighted
              ? 'border-[var(--gold)]/60 bg-gradient-to-br from-[var(--gold)]/15 to-[var(--bg-tertiary)] shadow-[0_0_8px_var(--gold)]/20'
              : 'border-[var(--border-glass)] bg-[var(--bg-glass)] shadow-[var(--shadow-soft)] opacity-25',
        )}
      >
        <div className="w-7 h-7 flex-shrink-0 rounded-lg flex items-center justify-center bg-[var(--gold)]/20">
          <ListChecks className="w-4 h-4 text-[var(--gold)]" />
        </div>
        <div className="min-w-0 flex-1">
          <div
            className="text-[0.7rem] font-semibold text-[var(--text-primary)] leading-tight truncate"
            title={data.label}
          >
            {data.label}
          </div>
          {data.detail && (
            <span className="inline-block mt-0.5 text-[0.55rem] text-[var(--gold)]/80 truncate">
              {data.detail}
            </span>
          )}
        </div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function WebNodeComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true

  const { width, height } = NODE_SIZES.web

  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        style={{ width, height, overflow: 'hidden' }}
        className={clsx(
          'flex items-center gap-2 px-2.5 py-1.5',
          'rounded-[12px] border',
          'transition-all duration-200 hover:shadow-[0_8px_16px_var(--shadow-glow)]',
          selected
            ? 'border-[var(--holo)] bg-gradient-to-br from-[var(--holo)]/20 to-[var(--bg-tertiary)] shadow-[0_0_12px_var(--holo)]/30'
            : isHighlighted
              ? 'border-[var(--holo)]/60 bg-gradient-to-br from-[var(--holo)]/15 to-[var(--bg-tertiary)] shadow-[0_0_8px_var(--holo)]/20'
              : 'border-[var(--border-glass)] bg-[var(--bg-glass)] shadow-[var(--shadow-soft)] opacity-25',
        )}
      >
        <div className="w-7 h-7 flex-shrink-0 rounded-lg flex items-center justify-center bg-[var(--holo)]/20">
          <Globe className="w-4 h-4 text-[var(--holo)]" />
        </div>
        <div className="min-w-0 flex-1">
          <div
            className="text-[0.7rem] font-semibold text-[var(--text-primary)] leading-tight truncate"
            title={data.label}
          >
            {data.label}
          </div>
          {data.model && (
            <span className="inline-block mt-0.5 text-[0.55rem] text-[var(--holo)]/80 font-mono truncate">
              {data.model}
            </span>
          )}
        </div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function ProfileNodeComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true
  const host = (data.host as string) || ''
  const { color, icon: Icon, platform, label: platformLabel } = profileVisual(data as Record<string, unknown>)
  const hostLine = platform ? `${platformLabel} · ${host || SOCIAL_PLATFORMS[platform].domains[0]}` : host

  const { width, height } = NODE_SIZES.profile

  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        style={{
          width,
          height,
          overflow: 'hidden',
          ...(selected
            ? {
                borderColor: color,
                background: `linear-gradient(to bottom right, color-mix(in oklab, ${color} 20%, transparent), var(--bg-tertiary))`,
                boxShadow: `0 0 12px color-mix(in oklab, ${color} 30%, transparent)`,
              }
            : isHighlighted
              ? {
                  borderColor: `color-mix(in oklab, ${color} 60%, transparent)`,
                  background: `linear-gradient(to bottom right, color-mix(in oklab, ${color} 15%, transparent), var(--bg-tertiary))`,
                  boxShadow: `0 0 8px color-mix(in oklab, ${color} 20%, transparent)`,
                }
              : {}),
        }}
        className={clsx(
          'flex items-center gap-2 px-2.5 py-1.5',
          'rounded-[12px] border',
          'transition-all duration-200 hover:shadow-[0_8px_16px_var(--shadow-glow)]',
          !selected && !isHighlighted && 'border-[var(--border-glass)] bg-[var(--bg-glass)] shadow-[var(--shadow-soft)] opacity-25',
        )}
      >
        <div
          className="w-6 h-6 flex-shrink-0 rounded-lg flex items-center justify-center"
          style={{ background: `color-mix(in oklab, ${color} 20%, transparent)`, color }}
        >
          <Icon className="w-3 h-3" />
        </div>
        <div className="min-w-0 flex-1">
          <div
            className="text-[0.65rem] font-semibold text-[var(--text-primary)] leading-tight truncate"
            title={data.label}
          >
            {data.label}
          </div>
          {hostLine && (
            <span
              className="inline-block mt-0.5 text-[0.5rem] font-mono truncate max-w-full"
              style={{ color: `color-mix(in oklab, ${color} 80%, transparent)` }}
            >
              {hostLine}
            </span>
          )}
        </div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

/**
 * Compact node components for LOD rendering (zoom out)
 * Shows a small colored dot/icon + 1-line label, ~110×28px
 */

export function DocumentNodeCompactComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true
  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        className={clsx(
          'flex items-center gap-1 px-2 py-1',
          'rounded-[6px] border text-[0.65rem] font-medium',
          'transition-all duration-200',
          selected
            ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-[0_0_8px_var(--accent-glow)]'
            : isHighlighted
              ? 'border-[var(--border-glass)] bg-[var(--accent-dim)]/20 shadow-[var(--shadow-soft)]'
              : 'border-[var(--border-glass)] bg-[var(--bg-glass)] opacity-25',
        )}
      >
        <FileText className="w-3 h-3 text-[var(--accent)] flex-shrink-0" />
        <div className="truncate text-[var(--text-primary)]">{data.shortLabel || data.label}</div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function QuestionNodeCompactComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true
  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        className={clsx(
          'flex items-center gap-1 px-2 py-1',
          'rounded-[6px] border text-[0.65rem] font-medium',
          'transition-all duration-200',
          selected
            ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-[0_0_8px_var(--accent-glow)]'
            : isHighlighted
              ? 'border-[var(--border-glass)] bg-[var(--accent-dim)]/20 shadow-[var(--shadow-soft)]'
              : 'border-[var(--border-glass)] bg-[var(--bg-glass)] opacity-25',
        )}
      >
        <MessageCircle className="w-3 h-3 text-[var(--accent)] flex-shrink-0" />
        <div className="truncate text-[var(--text-primary)]">{data.shortLabel || data.label}</div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function DecisionNodeCompactComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true
  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        className={clsx(
          'flex items-center gap-1 px-2 py-1',
          'rounded-[6px] border text-[0.65rem] font-medium',
          'transition-all duration-200',
          selected
            ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-[0_0_8px_var(--accent-glow)]'
            : isHighlighted
              ? 'border-[var(--border-glass)] bg-[var(--accent-dim)]/20 shadow-[var(--shadow-soft)]'
              : 'border-[var(--border-glass)] bg-[var(--bg-glass)] opacity-25',
        )}
      >
        <Sparkles className="w-3 h-3 text-[var(--accent)] flex-shrink-0" />
        <div className="truncate text-[var(--text-primary)]">{data.shortLabel || data.label}</div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function ModelNodeCompactComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true
  const isLaya = data.modelName === 'Laya'
  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        className={clsx(
          'flex items-center gap-1 px-2 py-1',
          'rounded-[6px] border text-[0.65rem] font-medium',
          'transition-all duration-200',
          isLaya && isHighlighted
            ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-[0_0_8px_var(--accent-glow)]'
            : selected
              ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-[0_0_8px_var(--accent-glow)]'
              : isHighlighted
                ? 'border-[var(--border-glass)] bg-[var(--accent-dim)]/20 shadow-[var(--shadow-soft)]'
                : 'border-[var(--border-glass)] bg-[var(--bg-glass)] opacity-25',
        )}
      >
        {isLaya ? (
          <Sparkles className="w-3 h-3 text-white flex-shrink-0" />
        ) : (
          <Cpu className="w-3 h-3 text-white flex-shrink-0" />
        )}
        <div className="truncate text-[var(--text-primary)]">{data.shortLabel || data.label}</div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function PlannerNodeCompactComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true
  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        className={clsx(
          'flex items-center gap-1 px-2 py-1',
          'rounded-[6px] border text-[0.65rem] font-medium',
          'transition-all duration-200',
          selected
            ? 'border-[var(--gold)] bg-[var(--gold)]/20 shadow-[0_0_8px_var(--gold)]/30'
            : isHighlighted
              ? 'border-[var(--gold)]/60 bg-[var(--gold)]/10 shadow-[var(--shadow-soft)]'
              : 'border-[var(--border-glass)] bg-[var(--bg-glass)] opacity-25',
        )}
      >
        <ListChecks className="w-3 h-3 text-[var(--gold)] flex-shrink-0" />
        <div className="truncate text-[var(--text-primary)]">{data.shortLabel || data.label}</div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function WebNodeCompactComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true
  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        className={clsx(
          'flex items-center gap-1 px-2 py-1',
          'rounded-[6px] border text-[0.65rem] font-medium',
          'transition-all duration-200',
          selected
            ? 'border-[var(--holo)] bg-[var(--holo)]/20 shadow-[0_0_8px_var(--holo)]/30'
            : isHighlighted
              ? 'border-[var(--holo)]/60 bg-[var(--holo)]/10 shadow-[var(--shadow-soft)]'
              : 'border-[var(--border-glass)] bg-[var(--bg-glass)] opacity-25',
        )}
      >
        <Globe className="w-3 h-3 text-[var(--holo)] flex-shrink-0" />
        <div className="truncate text-[var(--text-primary)]">{data.shortLabel || data.label}</div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

export function ProfileNodeCompactComponent({ data, selected }: NodeProps) {
  const isHighlighted = data.highlighted === true
  const { color, icon: Icon } = profileVisual(data as Record<string, unknown>)
  return (
    <>
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div
        style={
          selected
            ? { borderColor: color, background: `color-mix(in oklab, ${color} 20%, transparent)`, boxShadow: `0 0 8px color-mix(in oklab, ${color} 30%, transparent)` }
            : isHighlighted
              ? { borderColor: `color-mix(in oklab, ${color} 60%, transparent)`, background: `color-mix(in oklab, ${color} 10%, transparent)` }
              : undefined
        }
        className={clsx(
          'flex items-center gap-1 px-2 py-1',
          'rounded-[6px] border text-[0.65rem] font-medium',
          'transition-all duration-200',
          selected
            ? ''
            : isHighlighted
              ? 'shadow-[var(--shadow-soft)]'
              : 'border-[var(--border-glass)] bg-[var(--bg-glass)] opacity-25',
        )}
      >
        <Icon className="w-3 h-3 flex-shrink-0" style={{ color }} />
        <div className="truncate text-[var(--text-primary)]">{data.shortLabel || data.label}</div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </>
  )
}

/**
 * Entity node component for Knowledge Graph Entities view.
 * Fixed-size glass card, tinted per entity type, hidden handles on all sides so
 * the floating edges can attach anywhere.
 */
const ENTITY_TYPE_STYLE: Record<string, { color: string; label: string; icon: React.ElementType }> = {
  person: { color: 'var(--accent)', label: 'Person', icon: User },
  organization: { color: 'var(--gold)', label: 'Organization', icon: Building2 },
  role: { color: 'var(--holo)', label: 'Role', icon: Briefcase },
  document: { color: 'var(--text-secondary)', label: 'Document', icon: FileText },
  profile: { color: '#0a66c2', label: 'Profile', icon: Linkedin },
  location: { color: '#34d399', label: 'Location', icon: MapPin },
}

const ENTITY_HIDDEN_HANDLE: React.CSSProperties = { opacity: 0, pointerEvents: 'none', width: 1, height: 1, minWidth: 0, minHeight: 0, border: 0 }
const ENTITY_SIDES = [Position.Top, Position.Right, Position.Bottom, Position.Left] as const

export function EntityNodeComponent({ data, selected }: NodeProps) {
  const type = (data.entityType as string) || (data.type as string) || 'person'
  const style = ENTITY_TYPE_STYLE[type] || { color: 'var(--text-secondary)', label: type, icon: Layers }
  const host = typeof data.host === 'string' ? data.host : ''
  const profile = type === 'profile' ? profileVisual(data as Record<string, unknown>) : null
  const Icon = profile ? profile.icon : style.icon
  const platformLabel = typeof data.platformLabel === 'string' && data.platformLabel ? data.platformLabel : profile?.label || ''
  const { width, height } = entityNodeSize(type)
  const fullName = String(data.fullName ?? data.label ?? '')
  const title = String(data.displayName ?? data.label ?? '')
  const mentions = Number(data.mention_count) || 0
  const match = data.match === 'likely' ? 'likely' : data.match === 'candidate' ? 'candidate' : null
  const color = profile?.platform ? profile.color : style.color

  return (
    <>
      {ENTITY_SIDES.map(pos => (
        <React.Fragment key={pos}>
          <Handle id={`t-${pos}`} type="target" position={pos} isConnectable={false} style={ENTITY_HIDDEN_HANDLE} />
          <Handle id={`s-${pos}`} type="source" position={pos} isConnectable={false} style={ENTITY_HIDDEN_HANDLE} />
        </React.Fragment>
      ))}
      <div
        title={fullName}
        style={{
          width,
          height,
          borderColor: selected ? color : `color-mix(in oklab, ${color} 45%, transparent)`,
          background: `color-mix(in oklab, ${color} ${type === 'person' ? 14 : 8}%, transparent)`,
          boxShadow: selected ? `0 0 0 1px ${color}, 0 0 16px color-mix(in oklab, ${color} 45%, transparent)` : undefined,
        }}
        className="glass-card relative rounded-2xl border overflow-hidden flex items-center gap-2.5 pl-3.5 pr-2.5 py-2 transition-shadow duration-200"
      >
        {/* Type bar */}
        <span aria-hidden className="absolute left-0 top-0 bottom-0 w-1" style={{ background: color }} />
        <span
          className="flex-shrink-0 w-8 h-8 rounded-xl flex items-center justify-center"
          style={{ background: `color-mix(in oklab, ${color} 18%, transparent)`, color }}
        >
          <Icon className="w-4 h-4" />
        </span>
        <div className="min-w-0 flex-1 flex flex-col gap-0.5">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="text-[0.6rem] font-bold uppercase tracking-wider truncate" style={{ color }}>
              {data.focal ? 'Person · focus' : style.label}
            </span>
            {match ? (
              <span
                className={clsx(
                  'flex-shrink-0 px-1.5 py-px rounded-full text-[0.52rem] font-semibold uppercase tracking-wide border',
                  match === 'likely'
                    ? 'bg-[var(--accent-dim)] text-[var(--accent)] border-[var(--accent)]/40'
                    : 'text-[var(--text-muted)] border-[var(--border-glass)] border-dashed',
                )}
              >
                {match === 'likely' ? 'likely match' : 'candidate'}
              </span>
            ) : null}
            {mentions > 0 ? (
              <span
                className="ml-auto flex-shrink-0 px-1.5 py-px rounded-full bg-[var(--glass-fill-hover)] text-[0.55rem] font-mono text-[var(--text-muted)]"
                title={`${mentions} mention${mentions === 1 ? '' : 's'}`}
              >
                ×{mentions}
              </span>
            ) : null}
          </div>
          <div
            className={clsx(
              'font-semibold text-[var(--text-primary)] leading-tight line-clamp-2 break-words',
              type === 'person' ? 'text-[0.9rem]' : 'text-[0.8rem]',
            )}
          >
            {title}
          </div>
          {type === 'profile' && (host || platformLabel) ? (
            <div className="text-[0.6rem] text-[var(--text-muted)] truncate">
              {[platformLabel, host].filter(Boolean).join(' · ')}
            </div>
          ) : null}
        </div>
      </div>
    </>
  )
}

/**
 * Pipeline view (GraphRAG-style architecture diagram) — clean glass cards.
 * Colours by role, tinted with color-mix so they stay theme-aware.
 */
const PIPELINE_ROLE_COLORS: Record<string, string> = {
  question: '#34d399',
  answer: '#34d399',
  encoder: '#a78bfa',
  toolselect: 'var(--gold)',
  instruction: '#60a5fa',
  context: '#60a5fa',
  llm: 'var(--accent)',
  tool: '#f472b6',
  graph: '#fb923c',
  band: '#fb923c',
  source: 'var(--text-secondary)',
}

const PIPELINE_SIDES = [
  ['t', Position.Top],
  ['r', Position.Right],
  ['b', Position.Bottom],
  ['l', Position.Left],
] as const

const HIDDEN_HANDLE: React.CSSProperties = { opacity: 0, pointerEvents: 'none', width: 1, height: 1, minWidth: 0, minHeight: 0, border: 0 }

/** Invisible source + target handles on all four sides: ids `s-<side>` / `t-<side>`. */
function PipelineHandles() {
  return (
    <>
      {PIPELINE_SIDES.map(([side, pos]) => (
        <React.Fragment key={side}>
          <Handle id={`t-${side}`} type="target" position={pos} isConnectable={false} style={HIDDEN_HANDLE} />
          <Handle id={`s-${side}`} type="source" position={pos} isConnectable={false} style={HIDDEN_HANDLE} />
        </React.Fragment>
      ))}
    </>
  )
}

export function PipelineNodeComponent({ data, selected }: NodeProps) {
  const role = (data.role as string) || 'tool'
  const color = PIPELINE_ROLE_COLORS[role] || 'var(--accent)'
  const isTool = typeof data.used === 'boolean'
  const used = data.used !== false
  const details = ((data.details as string[] | undefined) || []).slice(0, (data.maxDetails as number) || 3)
  const chips = (data.chips as string[] | undefined) || []
  const titleClamp =
    role === 'question' || role === 'answer' ? 'line-clamp-3' : role === 'llm' ? 'line-clamp-4 break-all' : 'truncate'

  // Tool this run didn't use: a small grey chip (click for details).
  if (data.compact) {
    return (
      <>
        <PipelineHandles />
        <div
          className="w-full h-full rounded-full px-3 flex items-center gap-2 overflow-hidden text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
          style={{
            background: 'color-mix(in oklab, var(--text-muted) 8%, transparent)',
            border: '1px dashed color-mix(in oklab, var(--text-muted) 45%, transparent)',
            boxShadow: selected ? '0 0 0 1.5px var(--text-muted)' : undefined,
          }}
          title={`${data.kindLabel as string}: ${(data.details as string[] | undefined)?.join(' · ') || ''} — not used in this run`}
        >
          <span className="text-[12px] font-semibold truncate">{data.label as string}</span>
          <span className="ml-auto flex-shrink-0 text-[9.5px] uppercase tracking-[0.1em]">not used</span>
        </div>
      </>
    )
  }

  return (
    <>
      <PipelineHandles />
      <div
        className="glass-card relative w-full h-full rounded-2xl px-3 py-2.5 flex flex-col gap-1 overflow-hidden"
        style={{
          opacity: used ? 1 : 0.35,
          background: `color-mix(in oklab, ${color} 14%, transparent)`,
          borderColor: `color-mix(in oklab, ${color} ${used ? 55 : 45}%, transparent)`,
          borderStyle: used ? 'solid' : 'dashed',
          borderWidth: 1,
          boxShadow: selected
            ? `0 0 0 1.5px ${color}, 0 10px 28px -14px ${color}`
            : isTool && used
              ? `0 8px 24px -16px ${color}`
              : undefined,
        }}
        title={(data.fullText as string) || (data.label as string)}
      >
        <div className="flex shrink-0 items-center justify-between gap-2 min-w-0">
          <span className="text-[9px] font-bold uppercase tracking-[0.12em] truncate" style={{ color }}>
            {data.kindLabel as string}
          </span>
          {isTool && (
            <span
              className="flex-shrink-0 inline-flex items-center gap-0.5 rounded-full px-1.5 py-px text-[9px] font-semibold"
              style={
                used
                  ? { color, background: `color-mix(in oklab, ${color} 22%, transparent)` }
                  : { color: 'var(--text-muted)', border: '1px dashed var(--glass-stroke)' }
              }
            >
              {used && <Check className="w-2.5 h-2.5" />}
              {used ? 'used' : 'not used'}
            </span>
          )}
        </div>
        <div
          className={clsx('shrink-0 text-[13px] font-semibold leading-snug text-[var(--text-primary)]', titleClamp)}
          style={role === 'question' || role === 'answer' ? { fontSize: 12, fontWeight: 500 } : undefined}
        >
          {data.label as string}
        </div>
        {chips.length > 0 && (
          <div className="flex shrink-0 flex-wrap gap-1 mt-0.5">
            {chips.map(c => (
              <span
                key={c}
                className="max-w-full truncate rounded-full px-1.5 py-px text-[10px] leading-4 text-[var(--text-secondary)]"
                style={{ background: `color-mix(in oklab, ${color} 16%, transparent)`, border: `1px solid color-mix(in oklab, ${color} 35%, transparent)` }}
              >
                {c}
              </span>
            ))}
          </div>
        )}
        {details.map((d, i) => (
          <div key={i} className="shrink-0 text-[11px] leading-[15px] text-[var(--text-muted)] truncate" title={d}>
            {d}
          </div>
        ))}
      </div>
    </>
  )
}

/** Background band for the knowledge store — rendered behind the graph nodes. */
export function PipelineGroupNodeComponent({ data }: NodeProps) {
  const color = PIPELINE_ROLE_COLORS.band
  return (
    <div
      className="w-full h-full rounded-3xl px-4 py-3 pointer-events-none"
      style={{
        background: `color-mix(in oklab, ${color} 7%, transparent)`,
        border: `1.5px dashed color-mix(in oklab, ${color} 50%, transparent)`,
      }}
    >
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex items-baseline gap-2">
          <span className="section-label !text-[12px] !font-semibold" style={{ color }}>
            {data.label as string}
          </span>
          <span className="text-[9px] uppercase tracking-[0.12em] text-[var(--text-muted)]">{data.kindLabel as string}</span>
        </div>
        <span className="text-[11px] text-[var(--text-secondary)] font-mono">{data.stats as string}</span>
      </div>
    </div>
  )
}
