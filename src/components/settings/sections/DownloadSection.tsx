import { useEffect, useMemo, useRef, useState } from 'react'
import { Search, RefreshCw, DownloadCloud, Check, Loader2, X, Trash2, Wifi, WifiOff } from 'lucide-react'
import { clsx } from 'clsx'
import { Panel } from '@/components/ui/Panel'
import { fetchLibrary, pullModel, deleteModel, type LibraryModel, type LibraryResponse } from '@/lib/ollama'

const FILTERS: { key: string; label: string }[] = [
  { key: 'llm',       label: 'LLM' },
  { key: 'moe',       label: 'MoE' },
  { key: 'thinking',  label: 'Thinking' },
  { key: 'vision',    label: 'Vision' },
  { key: 'ocr',       label: 'OCR' },
  { key: 'code',      label: 'Code' },
  { key: 'embedding', label: 'Embedding' },
  { key: 'tools',     label: 'Tools' },
]

const CAP_COLORS: Record<string, string> = {
  moe: 'text-purple-300 border-purple-400/40 bg-purple-500/10',
  thinking: 'text-amber-300 border-amber-400/40 bg-amber-500/10',
  vision: 'text-sky-300 border-sky-400/40 bg-sky-500/10',
  ocr: 'text-teal-300 border-teal-400/40 bg-teal-500/10',
  code: 'text-emerald-300 border-emerald-400/40 bg-emerald-500/10',
  embedding: 'text-pink-300 border-pink-400/40 bg-pink-500/10',
  tools: 'text-indigo-300 border-indigo-400/40 bg-indigo-500/10',
  llm: 'text-[var(--text-muted)] border-[var(--border)] bg-[var(--bg-secondary)]',
}

export function DownloadSection() {
  const [data, setData] = useState<LibraryResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [active, setActive] = useState<Set<string>>(new Set())

  async function load(refresh = true) {
    setLoading(true); setError('')
    try {
      setData(await fetchLibrary(refresh))
    } catch (e: any) {
      setError(e.message ?? 'Failed to load library')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(true) }, [])

  function toggleFilter(k: string) {
    setActive(prev => { const n = new Set(prev); n.has(k) ? n.delete(k) : n.add(k); return n })
  }

  const models = data?.models ?? []
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return models.filter(m => {
      if (active.size > 0 && ![...active].every(f => m.capabilities.includes(f))) return false
      if (!q) return true
      return m.name.toLowerCase().includes(q) ||
        m.description.toLowerCase().includes(q) ||
        m.capabilities.some(c => c.includes(q)) ||
        m.family.toLowerCase().includes(q)
    })
  }, [models, query, active])

  return (
    <div className="space-y-5 max-w-3xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-serif text-xl text-[var(--text-primary)] mb-1">Download Models</h3>
          <p className="text-sm text-[var(--text-muted)]">
            Browse the latest Ollama models and install with one click — no terminal needed.
          </p>
        </div>
        <button onClick={() => load(true)} disabled={loading}
          className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[var(--border)] text-sm text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors flex-shrink-0">
          <RefreshCw className={clsx('w-3.5 h-3.5', loading && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {data && (
        <div className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
          {data.offline ? <WifiOff className="w-3.5 h-3.5 text-amber-400" /> : <Wifi className="w-3.5 h-3.5 text-emerald-400" />}
          {data.offline
            ? 'Offline — showing curated catalog (could not reach ollama.com)'
            : `Live from ollama.com · ${models.length} models`}
        </div>
      )}

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-muted)]" />
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search models…"
          className="w-full pl-9 pr-3 py-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)]" />
      </div>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map(f => (
          <button key={f.key} onClick={() => toggleFilter(f.key)}
            className={clsx('px-3 py-1.5 rounded-full text-xs border transition-colors',
              active.has(f.key)
                ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]')}>
            {f.label}
          </button>
        ))}
        {active.size > 0 && (
          <button onClick={() => setActive(new Set())} className="px-2 py-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--accent)]">Clear</button>
        )}
      </div>

      {error && <Panel className="px-4 py-3 border border-red-500/40 text-sm text-red-300">{error}</Panel>}

      {loading ? (
        <div className="space-y-2.5">{[1,2,3,4,5].map(i => <div key={i} className="h-24 rounded-xl bg-[var(--bg-tertiary)] animate-pulse" />)}</div>
      ) : filtered.length === 0 ? (
        <p className="text-center text-[var(--text-muted)] py-10 text-sm">No models match your search/filters.</p>
      ) : (
        <div className="space-y-2.5">
          {filtered.map(m => <ModelRow key={m.id} model={m} onChanged={() => load(false)} />)}
        </div>
      )}
    </div>
  )
}

function ModelRow({ model, onChanged }: { model: LibraryModel; onChanged: () => void }) {
  const [state, setState] = useState<'idle' | 'pulling' | 'done' | 'error'>(model.installed ? 'done' : 'idle')
  const [percent, setPercent] = useState(0)
  const [status, setStatus] = useState('')
  const [installed, setInstalled] = useState(model.installed)
  const abortRef = useRef<AbortController | null>(null)

  async function download() {
    if (state === 'pulling') { abortRef.current?.abort(); setState('idle'); setPercent(0); return }
    setState('pulling'); setPercent(0); setStatus('Connecting…')
    abortRef.current = new AbortController()
    try {
      for await (const p of pullModel(model.id, abortRef.current.signal)) {
        setStatus(p.status); setPercent(p.percent)
        if (p.done) {
          if (p.error) { setState('error') }
          else { setState('done'); setInstalled(true); onChanged() }
          return
        }
      }
      setState('done'); setInstalled(true); onChanged()
    } catch (e: any) {
      if (e?.name === 'AbortError') { setState('idle') } else { setState('error'); setStatus(e?.message ?? 'Failed') }
    }
  }

  async function remove() {
    if (!confirm(`Delete ${model.id}? This frees disk space and cannot be undone.`)) return
    const ok = await deleteModel(model.id)
    if (ok) { setInstalled(false); setState('idle'); setPercent(0); onChanged() }
  }

  const caps = model.capabilities.filter(c => c !== 'llm')
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)]/50 p-4 flex items-start gap-4">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium text-[var(--text-primary)] font-mono truncate">{model.name}</span>
          {model.params && <span className="text-[11px] text-[var(--text-muted)] font-mono">{model.params}</span>}
          {model.size_gb > 0 && <span className="text-[11px] text-[var(--text-muted)]">· {model.size_gb} GB</span>}
          {model.pulls && <span className="text-[11px] text-[var(--text-muted)]">· {model.pulls} pulls</span>}
        </div>
        {model.description && <p className="text-xs text-[var(--text-muted)] mt-1 leading-relaxed line-clamp-2">{model.description}</p>}
        <div className="flex flex-wrap gap-1.5 mt-2">
          <span className={clsx('text-[10px] px-1.5 py-0.5 rounded border uppercase tracking-wide', CAP_COLORS[model.type] ?? CAP_COLORS.llm)}>{model.type}</span>
          {caps.map(c => (
            <span key={c} className={clsx('text-[10px] px-1.5 py-0.5 rounded border', CAP_COLORS[c] ?? CAP_COLORS.llm)}>{c}</span>
          ))}
        </div>
        {state === 'pulling' && (
          <div className="mt-2.5">
            <div className="h-1.5 rounded-full bg-[var(--bg-secondary)] overflow-hidden">
              <div className="h-full bg-[var(--accent)] transition-all" style={{ width: `${percent}%` }} />
            </div>
            <div className="text-[10px] text-[var(--text-muted)] mt-1 font-mono truncate">{status} · {percent}%</div>
          </div>
        )}
      </div>
      <div className="flex-shrink-0 flex items-center gap-1.5">
        {installed ? (
          <>
            <span className="flex items-center gap-1 text-xs text-emerald-400"><Check className="w-3.5 h-3.5" /> Installed</span>
            <button onClick={remove} title="Delete model" className="p-1.5 text-[var(--text-muted)] hover:text-red-400 rounded-md hover:bg-red-500/10 transition-colors">
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </>
        ) : (
          <button onClick={download}
            className={clsx('flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
              state === 'pulling'
                ? 'border border-[var(--border)] text-[var(--text-muted)] hover:text-red-400'
                : 'bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)]')}>
            {state === 'pulling' ? <><X className="w-3.5 h-3.5" /> Cancel</>
              : state === 'error' ? <><DownloadCloud className="w-3.5 h-3.5" /> Retry</>
              : <><DownloadCloud className="w-3.5 h-3.5" /> Download</>}
          </button>
        )}
      </div>
    </div>
  )
}
