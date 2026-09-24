import { useState } from 'react'
import { motion } from 'framer-motion'
import {
  Loader2, CheckCircle2, AlertCircle, SkipForward, Eye, FileText, ScanLine, Bot, Brain, Sparkles,
  ChevronDown, ChevronRight,
} from 'lucide-react'
import { clsx } from 'clsx'
import type { Tile } from '@/lib/docAgent'

interface TileCardProps {
  tile: Tile
  now: number
}

const ICON_BY_KIND: Record<Tile['kind'], React.ElementType> = {
  laya: Brain,
  extract: FileText,
  ocr: ScanLine,
  llm: Bot,
  vision: Eye,
}

interface BadgeStyle {
  className: string
  label: string
}

const BADGES: Record<string, BadgeStyle> = {
  laya: {
    className: 'bg-[var(--accent-dim)] text-[var(--accent)]',
    label: 'laya',
  },
  rules: {
    className: 'bg-amber-500/20 text-amber-600',
    label: 'rules',
  },
  probe: {
    className: 'bg-blue-500/20 text-blue-600',
    label: 'probe',
  },
  config: {
    className: 'bg-[var(--bg-tertiary)] text-[var(--text-muted)]',
    label: 'config',
  },
  user: {
    className: 'bg-violet-500/20 text-violet-600',
    label: 'you',
  },
}

const FALLBACK_BADGE: BadgeStyle = {
  className: 'bg-[var(--bg-tertiary)] text-[var(--text-muted)]',
  label: '',
}

function formatMs(ms: number | null): string {
  if (ms === null) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

export function TileCard({ tile, now }: TileCardProps) {
  const [expandedDecision, setExpandedDecision] = useState<string | null>(null)

  const IconComp = ICON_BY_KIND[tile.kind]
  const isRunning = tile.status === 'running'
  const isDone = tile.status === 'done'
  const isError = tile.status === 'error'

  let elapsedMs: number | null = null
  if (isRunning && tile.started_ms !== null) {
    elapsedMs = Math.max(0, now - tile.started_ms)
  } else if (isDone) {
    elapsedMs = tile.ms
  }

  // Dynamic title based on status and kind
  let displayTitle = tile.title
  if (isDone) {
    if (tile.kind === 'laya') {
      displayTitle = 'Laya decisions'
    } else if (tile.kind === 'llm') {
      displayTitle = tile.model ? `Answer · ${tile.model}` : 'Answer'
    }
  } else if (isRunning) {
    if (tile.kind === 'laya') {
      displayTitle = 'Analyzing intent'
    } else if (tile.kind === 'llm') {
      displayTitle = 'Generating answer'
    }
  }

  const bgClass =
    isError
      ? 'bg-red-500/5 border-red-500/25'
      : 'bg-[var(--bg-secondary)]/60 border-[var(--border)]'

  return (
    <motion.div
      className={clsx('rounded-lg border p-3 space-y-3', bgClass)}
    >
      {/* Header */}
      <div className="flex items-start gap-2">
        <div className="flex-shrink-0 pt-0.5">
          <IconComp className="w-4 h-4 text-[var(--accent)]" />
        </div>

        <div className="flex-1 min-w-0">
          <div className="font-medium text-[var(--text-primary)] text-sm">{displayTitle}</div>
          {tile.detail && (
            <div className="text-xs text-[var(--text-muted)] mt-0.5">{tile.detail}</div>
          )}
        </div>

        {/* Status indicator */}
        <div className="flex-shrink-0 flex items-center gap-1.5">
          {isRunning && (
            <>
              <motion.div
                animate={{ rotate: 360 }}
                transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }}
                className="flex-shrink-0"
              >
                <Loader2 className="w-4 h-4 text-[var(--accent)]" />
              </motion.div>
              <span className="text-xs text-[var(--text-muted)] font-mono">
                {formatMs(elapsedMs)}
              </span>
            </>
          )}
          {isDone && (
            <>
              <CheckCircle2 className="w-4 h-4 text-green-500" />
              <span className="text-xs text-[var(--text-muted)] font-mono">{formatMs(elapsedMs)}</span>
            </>
          )}
          {tile.status === 'skipped' && <SkipForward className="w-4 h-4 text-[var(--text-muted)]" />}
          {isError && <AlertCircle className="w-4 h-4 text-red-500" />}
        </div>
      </div>

      {/* Model info row */}
      {tile.model_info && (
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <span className="font-mono text-[var(--text-secondary)] bg-[var(--bg-tertiary)] px-2 py-1 rounded">
            {tile.model || 'auto'}
          </span>
          {tile.kind === 'laya' && tile.model_info ? (
            <>
              {tile.model_info.name && (
                <span className="text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2 py-1 rounded">
                  {tile.model_info.name}
                </span>
              )}
              {tile.model_info.parameter_size && (
                <span className="text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2 py-1 rounded">
                  ~{tile.model_info.parameter_size}
                </span>
              )}
              {(tile.model_info as any)?.variant && (
                <span className="text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2 py-1 rounded">
                  {(tile.model_info as any).variant}
                </span>
              )}
              {(tile.model_info as any)?.type && (
                <span className="text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2 py-1 rounded">
                  {(tile.model_info as any).type}
                </span>
              )}
            </>
          ) : (
            <>
              {tile.model_info.family && (
                <span className="text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2 py-1 rounded">
                  {tile.model_info.family}
                </span>
              )}
              {tile.model_info.parameter_size && (
                <span className="text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2 py-1 rounded">
                  {tile.model_info.parameter_size}
                </span>
              )}
              {tile.model_info.quantization && (
                <span className="text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2 py-1 rounded">
                  {tile.model_info.quantization}
                </span>
              )}
              {tile.model_info.size_gb && (
                <span className="text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2 py-1 rounded">
                  {tile.model_info.size_gb.toFixed(1)} GB
                </span>
              )}
              {tile.model_info.is_vision && (
                <span className="text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2 py-1 rounded">
                  vision
                </span>
              )}
            </>
          )}
        </div>
      )}

      {/* Decisions list (only for laya kind) */}
      {tile.kind === 'laya' && tile.decisions.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs font-medium text-[var(--text-secondary)]">Decisions</div>
          <div className="space-y-1.5">
            {tile.decisions.map(decision => {
              const isExpanded = expandedDecision === decision.id
              const badgeStyle = BADGES[decision.source] ?? { ...FALLBACK_BADGE, label: decision.source }

              return (
                <div key={decision.id} className="space-y-1">
                  <div className="flex items-start gap-2">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-baseline gap-2">
                        <span className="text-xs text-[var(--text-secondary)]">{decision.label}</span>
                        <span className="font-medium text-[var(--text-primary)] text-sm">{decision.value}</span>
                      </div>
                      {decision.note && (
                        <div className="text-xs text-[var(--text-muted)] mt-0.5">{decision.note}</div>
                      )}
                    </div>
                    <span className={clsx('text-xs px-1.5 py-0.5 rounded font-medium flex-shrink-0', badgeStyle.className)}>
                      {badgeStyle.label}
                    </span>
                  </div>

                  {decision.confidence !== null && (
                    <div className="flex items-center justify-between gap-2 text-xs">
                      <div className="w-full h-1.5 bg-[var(--bg-tertiary)] rounded-full overflow-hidden">
                        <div
                          className="h-full bg-[var(--accent)]"
                          style={{ width: `${Math.min(100, decision.confidence * 100)}%` }}
                        />
                      </div>
                      <span className="text-[var(--text-muted)] font-mono flex-shrink-0">
                        {Math.round(decision.confidence * 100)}%
                      </span>
                    </div>
                  )}

                  {decision.probabilities && (
                    <button
                      onClick={() => setExpandedDecision(isExpanded ? null : decision.id)}
                      className="text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)] flex items-center gap-1 transition-colors"
                    >
                      {isExpanded ? (
                        <ChevronDown className="w-3 h-3" />
                      ) : (
                        <ChevronRight className="w-3 h-3" />
                      )}
                      {isExpanded ? 'Hide' : 'Show'} alternatives
                    </button>
                  )}

                  {isExpanded && decision.probabilities && (
                    <div className="space-y-1 pl-3 border-l border-[var(--border)]">
                      {Object.entries(decision.probabilities)
                        .sort((a, b) => (b[1] as number) - (a[1] as number))
                        .slice(0, 3)
                        .map(([label, prob]) => (
                          <div key={label} className="text-xs text-[var(--text-muted)]">
                            <div className="flex items-center justify-between gap-2">
                              <span>{label}</span>
                              <span className="font-mono flex-shrink-0">
                                {Math.round((prob as number) * 100)}%
                              </span>
                            </div>
                            <div className="w-full h-1 bg-[var(--bg-tertiary)] rounded-full overflow-hidden mt-0.5">
                              <div
                                className="h-full bg-[var(--accent-dim)]"
                                style={{ width: `${Math.min(100, (prob as number) * 100)}%` }}
                              />
                            </div>
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Output preview for extract/ocr/llm/vision */}
      {(tile.kind === 'extract' || tile.kind === 'ocr' || tile.kind === 'llm' || tile.kind === 'vision') && (
        <div className="space-y-1.5">
          {tile.doc && (tile.kind === 'extract' || tile.kind === 'ocr') && (
            <div className="text-xs text-[var(--text-muted)]">
              <span className="font-medium">{tile.doc.name}</span>
            </div>
          )}
          {tile.output_preview && (
            <details className="text-xs group">
              <summary className="cursor-pointer text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors font-medium select-none">
                {tile.kind === 'extract' || tile.kind === 'ocr' ? 'Preview' : 'Answer'}
              </summary>
              <pre className="text-xs text-[var(--text-muted)] mt-2 p-2 bg-[var(--bg-tertiary)]/40 rounded border border-[var(--border)] whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                {tile.output_preview}
              </pre>
            </details>
          )}
        </div>
      )}

      {/* Error message */}
      {isError && tile.detail && (
        <div className="text-xs text-red-500 bg-red-500/10 border border-red-500/25 rounded p-2">
          {tile.detail}
        </div>
      )}
    </motion.div>
  )
}
