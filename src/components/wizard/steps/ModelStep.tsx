import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Search, RefreshCw } from 'lucide-react'
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

export function ModelStep({ title, subtitle, category, selectedId, onSelect, ramGb = 0 }: ModelStepProps) {
  const [models, setModels] = useState<CatalogModel[]>([])
  const [installedModels, setInstalledModels] = useState<string[]>([])
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [tier, setTier] = useState('')

  async function fetchData() {
    setLoading(true)
    try {
      const [recRes, modRes] = await Promise.all([
        fetch('/api/setup/recommendations'),
        fetch('/api/models'),
      ])
      const rec = await recRes.json()
      const mods = await modRes.json()
      const installed: string[] = (mods.models ?? []).map((m: any) => m.name)
      setInstalledModels(installed)
      setTier(rec.tier ?? '')

      const list: CatalogModel[] = (rec.recommendations?.[category] ?? []).map((m: any) => ({
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

  useEffect(() => { fetchData() }, [category])

  // Also show currently installed Ollama models that match the category (but aren't in catalog)
  // For chat: show all installed non-embed, non-orpheus models
  const installedNotInCatalog = installedModels.filter(id => {
    if (category !== 'chat') return false
    const lower = id.toLowerCase()
    return !lower.includes('embed') && !lower.includes('orpheus') &&
           !models.some(m => m.id === id || id.startsWith(m.id.split(':')[0]))
  })

  const filtered = models.filter(m =>
    !filter || m.name.toLowerCase().includes(filter.toLowerCase()) ||
    m.description.toLowerCase().includes(filter.toLowerCase()) ||
    m.tags.some(t => t.includes(filter.toLowerCase()))
  )

  // Sort: installed first, then by RAM requirement (ascending)
  const sorted = [...filtered].sort((a, b) => {
    if (a.installed !== b.installed) return a.installed ? -1 : 1
    return a.ram_min_gb - b.ram_min_gb
  })

  return (
    <div className="max-w-2xl mx-auto space-y-5">
      <div>
        <h2 className="font-serif text-2xl text-[var(--text-primary)] mb-1">{title}</h2>
        <p className="text-sm text-[var(--text-muted)]">{subtitle}</p>
        {tier && (
          <p className="text-xs text-[var(--accent)] mt-1">
            Showing models recommended for your <strong>{tier}</strong> hardware tier
          </p>
        )}
      </div>

      {/* Search + refresh */}
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
