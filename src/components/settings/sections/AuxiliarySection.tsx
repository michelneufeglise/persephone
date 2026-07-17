import { useCallback, useEffect, useState } from 'react'
import {
  Zap, Search, Code2, Brain, Eye, ScrollText, FileJson,
  Heart, Feather, Sparkles, RefreshCw, Loader2, Save,
} from 'lucide-react'
import { Panel } from '@/components/ui/Panel'
import { Select } from '@/components/ui/Select'
import { useAppStore } from '@/store/appStore'
import { fetchModels } from '@/lib/ollama'

/**
 * Per-category auxiliary-model config. Each of the 10 delegate categories
 * has a preferred model — an empty string means "use the built-in default
 * ladder". The judge picks a category based on the user's send-to-worker
 * prompt; that category's configured model runs the task.
 */

interface Category {
  key:         string
  label:       string
  icon:        React.ElementType
  description: string
}

const CATEGORIES: Category[] = [
  { key: 'quick',        label: 'Quick',        icon: Zap,        description: 'One-line factual lookups. Fast turnaround.' },
  { key: 'general',      label: 'General',      icon: Sparkles,   description: 'Balanced default when nothing else is a strong fit.' },
  { key: 'research',     label: 'Research',     icon: Search,     description: 'Web-search-backed factual answers, cited sources.' },
  { key: 'code',         label: 'Code',         icon: Code2,      description: 'Programming, debugging, code review.' },
  { key: 'deep',         label: 'Deep',         icon: Brain,      description: 'Hard multi-step reasoning, proofs, complex analysis.' },
  { key: 'vision',       label: 'Vision',       icon: Eye,        description: 'Image / screenshot analysis. Requires a VLM.' },
  { key: 'long_context', label: 'Long Context', icon: ScrollText, description: 'Analysis of very long documents (128K+ context).' },
  { key: 'structured',   label: 'Structured',   icon: FileJson,   description: 'Strict JSON / tables / schema extraction.' },
  { key: 'emotional',    label: 'Emotional',    icon: Heart,      description: 'Empathic, warm, personal responses.' },
  { key: 'creative',     label: 'Creative',     icon: Feather,    description: 'Long-form prose, storytelling, marketing copy.' },
]

interface CategoryConfig {
  configured: string   // user's saved choice — "" if using ladder default
  resolved:   string   // what the backend would actually pick right now
}

export function AuxiliarySection() {
  const { models, setModels } = useAppStore()
  const [config, setConfig]     = useState<Record<string, CategoryConfig>>({})
  const [loading, setLoading]   = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [savingKey, setSavingKey] = useState<string | null>(null)
  const [error, setError]         = useState('')

  const loadConfig = useCallback(async () => {
    const r = await fetch('/api/delegate/config')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const d = await r.json() as { categories: Record<string, CategoryConfig> }
    setConfig(d.categories ?? {})
  }, [])

  const refresh = useCallback(async (showSpinner: boolean) => {
    if (showSpinner) setRefreshing(true)
    setError('')
    try {
      const [list] = await Promise.all([fetchModels(), loadConfig()])
      setModels(list)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      if (showSpinner) setRefreshing(false)
      setLoading(false)
    }
  }, [loadConfig, setModels])

  useEffect(() => { refresh(false) }, [refresh])

  const installedNames = models
    .map(m => m.name)
    .filter(n => !n.toLowerCase().includes('embed'))
    .sort((a, b) => a.localeCompare(b))

  function optionsFor(current: string) {
    const opts = installedNames.map(n => ({ value: n, label: n }))
    if (current && !installedNames.includes(current)) {
      opts.unshift({ value: current, label: `${current} (not installed)` })
    }
    opts.unshift({ value: '', label: 'Ladder default — auto-pick' })
    return opts
  }

  async function assign(category: string, model: string) {
    const prev = config[category]
    setConfig(c => ({ ...c, [category]: { configured: model, resolved: model || (prev?.resolved ?? '') } }))
    setSavingKey(category)
    setError('')
    try {
      const r = await fetch('/api/delegate/config', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ category, model }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      // Re-load so `resolved` reflects the true fallback if user cleared.
      await loadConfig()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      if (prev) setConfig(c => ({ ...c, [category]: prev }))
    } finally {
      setSavingKey(null)
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-serif text-xl text-[var(--text-primary)] mb-1">Auxiliary Models</h3>
          <p className="text-sm text-[var(--text-muted)]">
            When you click the amber <span className="inline-flex items-center rounded bg-amber-400/20 text-amber-300 px-1 mx-0.5">⚡</span>
            Send-to-Worker button, the judge model classifies your request
            into one of these categories, and the model configured here runs
            the task. Leave any category blank to fall back to the built-in
            ladder (which prefers big-capability MoE models — see the resolved
            column for what would actually run today).
          </p>
        </div>
        <button
          onClick={() => refresh(true)}
          disabled={refreshing}
          className="p-2 rounded-lg border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] transition-colors disabled:opacity-50"
          title="Refresh installed Ollama models + resolved defaults"
        >
          {refreshing ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 text-red-300 text-sm px-3 py-2">
          {error}
        </div>
      )}

      <div className="space-y-2">
        {CATEGORIES.map(cat => {
          const Icon    = cat.icon
          const c       = config[cat.key]
          const saving  = savingKey === cat.key
          const usingLadder = !c?.configured
          return (
            <Panel key={cat.key} className="p-3">
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 bg-[var(--accent-dim)]/40 border border-[var(--border)]">
                  <Icon className="w-4 h-4 text-[var(--accent)]" />
                </div>
                <div className="flex-1 min-w-0 space-y-2">
                  <div className="flex items-baseline gap-2">
                    <span className="text-[15px] text-[var(--text-primary)] font-medium">{cat.label}</span>
                    <span className="text-[10px] font-mono text-[var(--text-muted)] uppercase tracking-wider">
                      {cat.key}
                    </span>
                    {saving && <Loader2 className="w-3 h-3 animate-spin text-[var(--accent)]" />}
                  </div>
                  <div className="text-[12px] text-[var(--text-muted)] leading-snug">
                    {cat.description}
                  </div>
                  {loading ? (
                    <div className="text-[11px] text-[var(--text-muted)] italic">loading…</div>
                  ) : (
                    <>
                      <Select
                        value={c?.configured ?? ''}
                        onChange={v => assign(cat.key, v)}
                        options={optionsFor(c?.configured ?? '')}
                      />
                      {c?.resolved && (
                        <div className="text-[10.5px] font-mono text-[var(--text-muted)] flex items-center gap-1.5">
                          <Save className="w-2.5 h-2.5" />
                          {usingLadder ? 'ladder resolves to' : 'in use:'}
                          <span className={usingLadder ? 'text-[var(--text-secondary)]' : 'text-[var(--accent)]'}>
                            {c.resolved}
                          </span>
                          {!installedNames.includes(c.resolved) && (
                            <span className="text-amber-300 italic">(not installed — will fall back)</span>
                          )}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            </Panel>
          )
        })}
      </div>
    </div>
  )
}
