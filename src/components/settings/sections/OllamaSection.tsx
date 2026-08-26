import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Trash2, HardDrive, Loader2, Zap } from 'lucide-react'
import { clsx } from 'clsx'
import { Panel } from '@/components/ui/Panel'
import { fetchModels, deleteModel, fetchLoadedModels } from '@/lib/ollama'
import type { OllamaModel } from '@/types'

function fmtBytes(b: number): string {
  if (!b) return '—'
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(0)} MB`
  return `${(b / 1024 ** 3).toFixed(1)} GB`
}

export function OllamaSection() {
  const [models, setModels] = useState<OllamaModel[]>([])
  const [loaded, setLoaded] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')

  const load = useCallback(async (spin = false) => {
    if (spin) setLoading(true)
    setError('')
    try {
      const [list, ld] = await Promise.all([fetchModels(), fetchLoadedModels()])
      setModels(list.sort((a, b) => a.name.localeCompare(b.name)))
      setLoaded(ld)
    } catch (e: any) {
      setError(e.message ?? 'Failed to reach Ollama')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(true) }, [load])

  async function remove(name: string) {
    if (!confirm(`Delete ${name}? This frees disk space and cannot be undone.`)) return
    setBusy(name)
    try {
      if (await deleteModel(name)) setModels(prev => prev.filter(m => m.name !== name))
    } finally { setBusy(null) }
  }

  const totalBytes = models.reduce((a, m) => a + (m.size ?? 0), 0)

  return (
    <div className="space-y-5 max-w-2xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-serif text-xl text-[var(--text-primary)] mb-1">Ollama — Installed Models</h3>
          <p className="text-sm text-[var(--text-muted)]">Everything pulled into your local Ollama. Delete what you no longer need.</p>
        </div>
        <button onClick={() => load(true)} disabled={loading}
          className="flex items-center gap-2 px-3 py-2 rounded-lg border border-[var(--border)] text-sm text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors flex-shrink-0">
          <RefreshCw className={clsx('w-3.5 h-3.5', loading && 'animate-spin')} /> Refresh
        </button>
      </div>

      <Panel className="px-4 py-3 flex items-center gap-2 text-xs font-mono uppercase tracking-[0.18em] text-[var(--text-muted)]">
        <HardDrive className="w-3.5 h-3.5" />
        {models.length} model{models.length === 1 ? '' : 's'} · {fmtBytes(totalBytes)} on disk
      </Panel>

      {error && <Panel className="px-4 py-3 border border-red-500/40 text-sm text-red-300">{error}</Panel>}

      {loading ? (
        <div className="space-y-2.5">{[1,2,3].map(i => <div key={i} className="h-16 rounded-xl bg-[var(--bg-tertiary)] animate-pulse" />)}</div>
      ) : models.length === 0 ? (
        <p className="text-center text-[var(--text-muted)] py-10 text-sm">No models installed. Use the Download tab to add some.</p>
      ) : (
        <div className="space-y-2.5">
          {models.map(m => {
            const isLoaded = loaded.includes(m.name)
            return (
              <div key={m.name} className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)]/50 p-4 flex items-center gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-[var(--text-primary)] font-mono truncate">{m.name}</span>
                    {isLoaded && <span className="flex items-center gap-1 text-[10px] text-amber-300 border border-amber-400/40 bg-amber-500/10 px-1.5 py-0.5 rounded"><Zap className="w-3 h-3" /> loaded</span>}
                  </div>
                  <div className="text-xs text-[var(--text-muted)] mt-1 font-mono">
                    {fmtBytes(m.size ?? 0)}
                    {m.details?.parameter_size ? ` · ${m.details.parameter_size}` : ''}
                    {m.details?.quantization_level ? ` · ${m.details.quantization_level}` : ''}
                    {m.details?.family ? ` · ${m.details.family}` : ''}
                  </div>
                </div>
                <button onClick={() => remove(m.name)} disabled={busy === m.name}
                  className="flex-shrink-0 p-2 text-[var(--text-muted)] hover:text-red-400 rounded-md hover:bg-red-500/10 transition-colors disabled:opacity-50">
                  {busy === m.name ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
