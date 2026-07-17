import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Search, RefreshCw, Filter, Zap } from 'lucide-react'
import { ModelCard, type CatalogModel } from '../ModelCard'

interface ModelStepProps {
  title: string
  subtitle: string
  category: 'chat' | 'vision' | 'code' | 'embed' | 'ocr' | 'docs' | 'handwriting' | 'tables' | 'judge'
  selectedId: string
  onSelect: (id: string) => void
  ramGb?: number
  optional?: boolean
}

interface HardwareProfile {
  cpu:                  string
  chip_family:          string
  chip_variant:         string
  ram_gb:               number
  mem_bandwidth_gb_s:   number
  tier:                 string
}

export function ModelStep({ title, subtitle, category, selectedId, onSelect, ramGb = 0 }: ModelStepProps) {
  const [models, setModels]     = useState<CatalogModel[]>([])
  const [installedModels, setInstalledModels] = useState<string[]>([])
  const [profile, setProfile]   = useState<HardwareProfile | null>(null)
  const [filter, setFilter]     = useState('')
  const [hideSlow, setHideSlow] = useState(true)  // default: only show models meeting the 20 tok/s target
  const [loading, setLoading]   = useState(true)
  const [tier, setTier]         = useState('')
  const [minTokPerS, setMinTokPerS] = useState(20)

  async function fetchData() {
    setLoading(true)
    try {
      // Prefer the new hardware-aware endpoint. Falls back to the legacy
      // tier-based recommendations if the new one isn't available yet.
      const [optRes, modRes] = await Promise.all([
        fetch(`/api/setup/optimized-models?min_tok_per_s=${minTokPerS}`),
        fetch('/api/models'),
      ])
      const opt  = await optRes.json()
      const mods = await modRes.json()
      const installed: string[] = (mods.models ?? []).map((m: any) => m.name)
      setInstalledModels(installed)
      setProfile(opt.profile ?? null)
      setTier(opt.profile?.tier ?? '')

      // The new endpoint returns categories keyed by the same names as the
      // legacy one, so the rest of the flow is drop-in compatible.
      const raw = (opt.categories?.[category] ?? []) as any[]
      const list: CatalogModel[] = raw.map(m => ({
        ...m,
        installed: installed.some(id => id === m.id || id.startsWith(m.id.split(':')[0])),
      }))
      setModels(list)
    } catch {
      setModels([])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchData() /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [category, minTokPerS])

  // Also show currently installed Ollama models that match the category (but aren't in catalog)
  // For chat: show all installed non-embed, non-orpheus models
  const installedNotInCatalog = installedModels.filter(id => {
    if (category !== 'chat') return false
    const lower = id.toLowerCase()
    return !lower.includes('embed') && !lower.includes('orpheus') &&
           !models.some(m => m.id === id || id.startsWith(m.id.split(':')[0]))
  })

  const filtered = models.filter(m => {
    const matchText = !filter || m.name.toLowerCase().includes(filter.toLowerCase()) ||
      m.description.toLowerCase().includes(filter.toLowerCase()) ||
      m.tags.some(t => t.includes(filter.toLowerCase()))
    if (!matchText) return false
    // Embedding models don't have a meaningful tok/s (they're batch models).
    // Only filter by speed for chat/code/vision/reasoning categories.
    if (hideSlow && category !== 'embed' && category !== 'judge') {
      if (m.fit === 'slow' || m.fit === 'unsupported') return false
    }
    return true
  })

  // Sort: installed first, then by tok/s desc (new endpoint), else by RAM asc.
  const sorted = [...filtered].sort((a, b) => {
    if (a.installed !== b.installed) return a.installed ? -1 : 1
    const at = a.tok_per_s_est ?? -1
    const bt = b.tok_per_s_est ?? -1
    if (at !== bt) return bt - at
    return a.ram_min_gb - b.ram_min_gb
  })

  const hiddenSlowCount = models.filter(
    m => m.fit === 'slow' || m.fit === 'unsupported',
  ).length

  return (
    <div className="max-w-2xl mx-auto space-y-5">
      <div>
        <h2 className="font-serif text-2xl text-[var(--text-primary)] mb-1">{title}</h2>
        <p className="text-sm text-[var(--text-muted)]">{subtitle}</p>
        {profile ? (
          <div className="mt-2 text-xs text-[var(--text-muted)] flex flex-wrap items-center gap-1.5">
            <span>Your hardware:</span>
            <span className="font-mono text-[var(--accent)]">{profile.cpu}</span>
            <span>·</span>
            <span className="font-mono">{profile.ram_gb}GB RAM</span>
            <span>·</span>
            <span className="font-mono">~{profile.mem_bandwidth_gb_s} GB/s</span>
            <span>·</span>
            <span className="uppercase tracking-wider text-[var(--accent)]">{profile.tier}</span>
            <span className="text-[var(--text-muted)]/60">
              — targeting ≥{minTokPerS} tok/s
            </span>
          </div>
        ) : tier ? (
          <p className="text-xs text-[var(--accent)] mt-1">
            Showing models recommended for your <strong>{tier}</strong> hardware tier
          </p>
        ) : null}
      </div>

      {/* Search + refresh + speed filter */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-muted)]" />
          <input
            value={filter}
            onChange={e => setFilter(e.target.value)}
            placeholder="Filter models…"
            className="w-full pl-9 pr-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)]
              text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)]
              focus:outline-none focus:border-[var(--accent)] transition-colors"
          />
        </div>
        {category !== 'embed' && category !== 'judge' && (
          <button
            onClick={() => setHideSlow(v => !v)}
            className={`px-3 py-2 rounded-lg border text-xs font-medium transition-colors flex items-center gap-1.5 ${
              hideSlow
                ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--accent-dim)]/40'
                : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)]'
            }`}
            title={hideSlow
              ? `Showing only models running ≥ ${minTokPerS} tok/s on your hardware. Click to reveal ${hiddenSlowCount} slower models.`
              : `Hide models running below ${minTokPerS} tok/s`}
          >
            {hideSlow ? <Zap className="w-3.5 h-3.5" /> : <Filter className="w-3.5 h-3.5" />}
            {hideSlow ? `≥${minTokPerS} tok/s` : `all speeds`}
            {hideSlow && hiddenSlowCount > 0 && (
              <span className="text-[9px] font-mono opacity-60">({hiddenSlowCount} hidden)</span>
            )}
          </button>
        )}
        <button
          onClick={fetchData}
          className="p-2 rounded-lg border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--accent)]
            hover:border-[var(--accent)] hover:bg-[var(--accent-dim)] transition-all"
          title="Refresh"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {/* Already-installed models not in catalog */}
      {category === 'chat' && installedNotInCatalog.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs text-[var(--text-muted)] uppercase tracking-wide">Already installed on your Ollama</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {installedNotInCatalog.map(id => (
              <motion.button
                key={id}
                initial={{ opacity: 0 }} animate={{ opacity: 1 }}
                onClick={() => onSelect(id)}
                className={`flex items-center gap-2 px-3 py-2.5 rounded-lg border text-sm text-left transition-all ${
                  selectedId === id
                    ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                    : 'border-[var(--border)] text-[var(--text-primary)] hover:border-[var(--border-bright)]'
                }`}
              >
                <span className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
                <span className="truncate font-mono text-xs">{id}</span>
              </motion.button>
            ))}
          </div>
        </div>
      )}

      {/* Catalog models */}
      {loading ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {[1, 2, 3, 4].map(i => (
            <div key={i} className="h-40 rounded-xl bg-[var(--bg-tertiary)] animate-pulse" />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        <p className="text-center text-[var(--text-muted)] py-8">No models found for your search.</p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 max-h-[420px] overflow-y-auto pr-1"
          style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--scrollbar) transparent' }}>
          {sorted.map(model => (
            <ModelCard
              key={model.id}
              model={model}
              selected={selectedId === model.id}
              onSelect={() => onSelect(model.id)}
              ramGb={ramGb}
            />
          ))}
        </div>
      )}

      {category !== 'embed' && (
        <p className="text-xs text-[var(--text-muted)]">
          You can skip this step and add models later from Settings → Model.
        </p>
      )}
    </div>
  )
}
