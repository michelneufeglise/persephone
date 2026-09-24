import React, { useMemo } from 'react'
import { FileText, Image, Mail, File, Table2, Type, Layers, FileJson, MessageCircle, Sparkles, Brain, Cpu } from 'lucide-react'
import { clsx } from 'clsx'
import { Handle, Position } from '@xyflow/react'
import { NODE_SIZES } from './kgModel'

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
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} />
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
            <span className="inline-block mt-0.5 text-[0.55rem] uppercase tracking-wider font-medium text-[var(--text-muted)] bg-[var(--bg-secondary)]/50 px-1.5 py-px rounded-full">
              {kind}
            </span>
          )}
        </div>
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} />
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
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} />
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
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} />
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
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} />
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
          <div className="text-[0.5rem] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-full bg-[var(--bg-secondary)]/60 text-[var(--text-muted)]">
            {sourceLabels[data.source as keyof typeof sourceLabels] || data.source}
          </div>
        </div>
        <div className="text-[0.65rem] font-semibold text-[var(--text-primary)] line-clamp-2 leading-tight">
          {data.label}
        </div>
        {data.confidence !== null && data.confidence !== undefined && (
          <div className="w-full h-1 bg-[var(--bg-secondary)]/40 rounded-full overflow-hidden mt-0.5">
            <div
              className="h-full bg-gradient-to-r from-[var(--accent)] to-[var(--accent-deep)] transition-all duration-300"
              style={{ width: `${Math.min(100, Math.max(0, data.confidence * 100))}%` }}
            />
          </div>
        )}
      </div>
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} />
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
      <Handle type="target" position={data.direction === 'LR' ? Position.Left : Position.Top} />
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
      <Handle type="source" position={data.direction === 'LR' ? Position.Right : Position.Bottom} />
    </>
  )
}
