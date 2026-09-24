import { useEffect, useState, useCallback } from 'react'
import { AlertCircle, CheckCircle2, SkipForward, AlertTriangle, Loader2, ChevronDown, ChevronRight } from 'lucide-react'
import { clsx } from 'clsx'
import { routeDocument, layaStatus } from '@/lib/idp'
import { fetchModels } from '@/lib/ollama'
import type { RouteResult } from '@/lib/idp'
import type { OllamaModel } from '@/types'

interface RoutingGraphProps {
  docId: string
}

const STATUS_COLORS: Record<string, { bg: string; icon: React.ElementType; border: string }> = {
  ok: { bg: 'bg-green-500/10', icon: CheckCircle2, border: 'border-green-500/40' },
  skipped: { bg: 'bg-blue-500/10', icon: SkipForward, border: 'border-blue-500/40' },
  fallback: { bg: 'bg-amber-500/10', icon: AlertTriangle, border: 'border-amber-500/40' },
  error: { bg: 'bg-red-500/10', icon: AlertCircle, border: 'border-red-500/40' },
}

function TraceNode({ step, index }: { step: any; index: number }) {
  const config = STATUS_COLORS[step.status] || STATUS_COLORS.ok
  const Icon = config.icon
  return (
    <div className={clsx('rounded-lg border', config.bg, config.border, 'p-3 text-sm space-y-1')}>
      <div className="flex items-start gap-2">
        <Icon className="w-4 h-4 flex-shrink-0 mt-0.5 text-[var(--text-secondary)]" />
        <div className="flex-1 min-w-0">
          <div className="font-medium text-[var(--text-primary)] capitalize">{step.node}</div>
          <div className="text-xs text-[var(--text-muted)]">{step.detail}</div>
        </div>
        <div className="text-xs text-[var(--text-muted)] font-mono flex-shrink-0 whitespace-nowrap">{step.ms}ms</div>
      </div>
    </div>
  )
}

function DecideNode({ decision }: { decision: any }) {
  const config = STATUS_COLORS.ok
  const Icon = config.icon
  const sorted = Object.entries(decision.probabilities)
    .sort((a, b) => (b[1] as number) - (a[1] as number))
    .slice(0, 3)

  return (
    <div className={clsx('rounded-lg border', config.bg, config.border, 'p-3 text-sm space-y-2')}>
      <div className="flex items-start gap-2">
        <Icon className="w-4 h-4 flex-shrink-0 mt-0.5 text-[var(--text-secondary)]" />
        <div className="flex-1 min-w-0">
          <div className="font-medium text-[var(--text-primary)]">Decide (Laya)</div>
          <div className="text-xs text-[var(--text-muted)]">{decision.kind}</div>
        </div>
      </div>
      {decision.confidence !== null && (
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-xs text-[var(--text-muted)]">Confidence</span>
            <span className="text-xs text-[var(--text-secondary)] font-mono">{(decision.confidence * 100).toFixed(0)}%</span>
          </div>
          <div className="w-full h-1.5 bg-[var(--bg-tertiary)] rounded-full overflow-hidden">
            <div
              className="h-full bg-[var(--accent)]"
              style={{ width: `${Math.min(100, (decision.confidence * 100))}%` }}
            />
          </div>
        </div>
      )}
      {sorted.length > 0 && (
        <div className="space-y-0.5">
          <div className="text-xs text-[var(--text-muted)]">Top matches</div>
          {sorted.map(([label, prob]) => (
            <div key={label} className="text-xs text-[var(--text-secondary)] flex justify-between">
              <span>{label}</span>
              <span className="font-mono text-[var(--text-muted)]">{((prob as number) * 100).toFixed(0)}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ModelNode({ model, category, overridden }: { model: string | null; category: string; overridden: boolean }) {
  const config = STATUS_COLORS.ok
  const Icon = config.icon
  return (
    <div className={clsx('rounded-lg border', config.bg, config.border, 'p-3 text-sm space-y-1')}>
      <div className="flex items-start gap-2">
        <Icon className="w-4 h-4 flex-shrink-0 mt-0.5 text-[var(--text-secondary)]" />
        <div className="flex-1 min-w-0">
          <div className="font-medium text-[var(--text-primary)]">Model</div>
          <div className="text-xs text-[var(--text-muted)] font-mono truncate">{model || 'auto'}</div>
          {category && (
            <div className="text-xs text-[var(--text-muted)] mt-0.5 capitalize">{category}</div>
          )}
        </div>
      </div>
      {overridden && (
        <div className="text-xs bg-[var(--accent-dim)] text-[var(--accent)] px-2 py-1 rounded inline-block">
          overridden
        </div>
      )}
    </div>
  )
}

export function RoutingGraph({ docId }: RoutingGraphProps) {
  const [route, setRoute] = useState<RouteResult | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [models, setModels] = useState<OllamaModel[]>([])
  const [selectedModel, setSelectedModel] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [showProbe, setShowProbe] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [r, mods] = await Promise.all([
        routeDocument(docId),
        fetchModels(),
      ])
      setRoute(r)
      setModels(mods)
      setSelectedModel(r.model ?? '')
    } catch (e: any) {
      setError(e.message ?? 'Failed to load routing')
      setLoading(false)
    } finally {
      setLoading(false)
    }
  }, [docId])

  useEffect(() => {
    load()
  }, [load])

  const handleOverride = useCallback(async (modelId: string) => {
    if (!route) return
    setApplying(true)
    try {
      const newRoute = await routeDocument(docId, {
        overrideModel: modelId || null,
      })
      setRoute(newRoute)
      setSelectedModel(modelId || '')
    } catch (e: any) {
      setError(e.message ?? 'Failed to apply override')
    } finally {
      setApplying(false)
    }
  }, [docId, route])

  if (loading) {
    return (
      <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)]/50 p-6 flex items-center justify-center gap-2 text-[var(--text-muted)]">
        <Loader2 className="w-4 h-4 animate-spin" />
        <span className="text-sm">Loading routing…</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4">
        <p className="text-sm text-amber-300">{error}</p>
        <button
          onClick={load}
          className="mt-3 text-xs text-amber-400 hover:text-amber-300 px-3 py-1.5 rounded hover:bg-amber-500/10 transition-colors"
        >
          Try again
        </button>
      </div>
    )
  }

  if (!route) {
    return (
      <div className="text-center p-6 text-[var(--text-muted)] text-sm">
        No routing data available
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Trace flow */}
      <div className="space-y-3">
        <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider font-medium">Decision trace</div>
        <div className="space-y-2">
          {route.trace.map((step, i) => (
            step.node === 'decide' ? (
              <DecideNode key={i} decision={route.decision} />
            ) : (
              <TraceNode key={i} step={step} index={i} />
            )
          ))}
          <ModelNode model={route.model} category={route.category} overridden={route.overridden} />
        </div>
      </div>

      {/* Reason & facts */}
      <div className="space-y-2.5">
        <div>
          <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider font-medium mb-1.5">Reason</div>
          <p className="text-sm text-[var(--text-secondary)] leading-relaxed">{route.reason}</p>
        </div>

        <div>
          <button
            onClick={() => setShowProbe(!showProbe)}
            className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
          >
            {showProbe ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            Document facts ({route.probe ? `${route.probe.pages} pg, ${route.probe.chars} chr` : '—'})
          </button>
          {showProbe && route.probe && (
            <div className="mt-2 text-xs text-[var(--text-muted)] space-y-1 pl-5">
              <div>MIME: <span className="text-[var(--text-secondary)] font-mono">{route.probe.mime}</span></div>
              <div>Pages: <span className="text-[var(--text-secondary)]">{route.probe.pages}</span></div>
              <div>Characters: <span className="text-[var(--text-secondary)]">{route.probe.chars}</span></div>
              <div>Text layer: <span className="text-[var(--text-secondary)]">{route.probe.has_text_layer ? 'yes' : 'no'}</span></div>
              <div>Images: <span className="text-[var(--text-secondary)]">{route.probe.has_page_images ? 'yes' : 'no'}</span></div>
              <div>Table density: <span className="text-[var(--text-secondary)]">{(route.probe.table_density * 100).toFixed(0)}%</span></div>
              <div>Email: <span className="text-[var(--text-secondary)]">{route.probe.is_email ? 'yes' : 'no'}</span></div>
            </div>
          )}
        </div>
      </div>

      {/* Model override */}
      <div className="space-y-2.5">
        <div>
          <label className="text-xs text-[var(--text-muted)] uppercase tracking-wider font-medium block mb-1.5">
            Model override
          </label>
          <div className="flex gap-2">
            <select
              value={selectedModel ?? ''}
              onChange={e => setSelectedModel(e.target.value || null)}
              disabled={applying}
              className="flex-1 px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)] disabled:opacity-50"
            >
              <option value="">Auto (use router)</option>
              {models.map(m => (
                <option key={m.name} value={m.name}>{m.name}</option>
              ))}
            </select>
            <button
              onClick={() => handleOverride(selectedModel ?? '')}
              disabled={applying}
              className="px-3 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] transition-colors disabled:opacity-50 flex-shrink-0"
            >
              {applying ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Apply'}
            </button>
          </div>
        </div>
        <button
          onClick={() => load()}
          disabled={loading}
          className="text-xs text-[var(--text-muted)] hover:text-[var(--accent)] px-3 py-1.5 rounded hover:bg-[var(--bg-tertiary)] transition-colors"
        >
          Re-route
        </button>
      </div>
    </div>
  )
}
