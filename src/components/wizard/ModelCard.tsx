import { useState, useRef, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Download, Check, Cpu, MemoryStick, ExternalLink, X, AlertTriangle } from 'lucide-react'
import { clsx } from 'clsx'

export interface CatalogModel {
  id: string
  name: string
  family: string
  params: string
  ram_min_gb: number
  quant: string
  category: string
  description: string
  tags: string[]
  hf_url: string
  size_gb: number
  tiers: string[]
  installed: boolean
  /** Estimated tokens/sec on the current hardware (from /api/setup/optimized-models). */
  tok_per_s_est?: number
  /** Fit rating: 'top' | 'good' | 'acceptable' | 'slow' | 'unsupported'. */
  fit?: string
  /** True when tok_per_s_est ≥ min target (usually 20). */
  meets_target?: boolean
}

interface ModelCardProps {
  model: CatalogModel
  selected?: boolean
  onSelect: () => void
  ramGb?: number
}

interface PullChunk {
  status?: string
  digest?: string
  total?: number
  completed?: number
  error?: string
}

export function ModelCard({ model, selected, onSelect, ramGb = 0 }: ModelCardProps) {
  const [downloadState, setDownloadState] = useState<'idle' | 'downloading' | 'done' | 'error'>('idle')
  const [progress, setProgress] = useState(0)
  const [statusText, setStatusText] = useState('')
  const [installed, setInstalled] = useState(model.installed)
  const abortRef = useRef<AbortController | null>(null)
  const canRun = ramGb === 0 || ramGb >= model.ram_min_gb

  async function handleDownload(e: React.MouseEvent) {
    e.stopPropagation()
    if (downloadState === 'downloading') {
      abortRef.current?.abort()
      setDownloadState('idle')
      setProgress(0)
      return
    }

    setDownloadState('downloading')
    setProgress(0)
    setStatusText('Connecting…')
    abortRef.current = new AbortController()

    try {
      const res = await fetch('/api/models/pull', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: model.id }),
        signal: abortRef.current.signal,
      })

      const reader = res.body!.getReader()
      const dec = new TextDecoder()
      let buf = ''
      const layerTotals: Record<string, number> = {}
      const layerDone: Record<string, number> = {}

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6).trim()
          if (payload === '[DONE]') { setDownloadState('done'); setInstalled(true); setProgress(100); return }
          try {
            const chunk = JSON.parse(payload) as PullChunk
            if (chunk.error) { setDownloadState('error'); setStatusText(chunk.error); return }
            if (chunk.status) setStatusText(chunk.status)
            if (chunk.digest && chunk.total) {
              layerTotals[chunk.digest] = chunk.total
              layerDone[chunk.digest] = chunk.completed ?? 0
              const total = Object.values(layerTotals).reduce((a, b) => a + b, 0)
              const done2 = Object.values(layerDone).reduce((a, b) => a + b, 0)
              if (total > 0) setProgress(Math.round((done2 / total) * 100))
            }
          } catch { /* skip */ }
        }
      }
      setDownloadState('done')
      setInstalled(true)
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setDownloadState('error')
        setStatusText(err.message)
      } else {
        setDownloadState('idle')
      }
    }
  }

  const isReady = installed || downloadState === 'done'

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      onClick={onSelect}
      className={clsx(
        'relative rounded-xl border p-3.5 cursor-pointer transition-all duration-200 group',
        selected
          ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-lg shadow-[var(--accent-glow)]'
          : 'border-[var(--border)] bg-[var(--bg-tertiary)] hover:border-[var(--border-bright)]',
        !canRun && 'opacity-60',
      )}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-sm font-semibold text-[var(--text-primary)]">{model.name}</span>
            {isReady && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-green-500/20 text-green-400 font-medium">
                Installed
              </span>
            )}
            {!canRun && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-red-500/20 text-red-400 font-medium flex items-center gap-0.5">
                <AlertTriangle className="w-2.5 h-2.5" />Needs {model.ram_min_gb}GB
              </span>
            )}
          </div>
          <p className="text-[11px] text-[var(--text-muted)] mt-0.5">{model.family}</p>
        </div>

        {/* Selected indicator */}
        {selected && (
          <div className="w-5 h-5 rounded-full bg-[var(--accent)] flex items-center justify-center flex-shrink-0">
            <Check className="w-3 h-3 text-white" />
          </div>
        )}
      </div>

      {/* Description */}
      <p className="text-[12px] text-[var(--text-secondary)] leading-relaxed mb-2.5">{model.description}</p>

      {/* Meta row */}
      <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)] mb-2.5 flex-wrap">
        <span className="flex items-center gap-1"><Cpu className="w-3 h-3" />{model.params}</span>
        <span className="flex items-center gap-1"><MemoryStick className="w-3 h-3" />≥{model.ram_min_gb}GB RAM</span>
        <span className="font-mono">{model.quant}</span>
        <span>~{model.size_gb}GB dl</span>
        {model.tok_per_s_est !== undefined && (
          <span
            className={clsx(
              'font-mono font-medium px-1.5 py-0.5 rounded-full border',
              model.fit === 'top' && 'text-emerald-300 border-emerald-500/40 bg-emerald-500/10',
              model.fit === 'good' && 'text-green-300 border-green-500/40 bg-green-500/10',
              model.fit === 'acceptable' && 'text-amber-300 border-amber-500/40 bg-amber-500/10',
              model.fit === 'slow' && 'text-red-300 border-red-500/40 bg-red-500/10',
              model.fit === 'unsupported' && 'text-red-400 border-red-500/50 bg-red-500/15',
            )}
            title={
              model.fit === 'top'         ? 'Runs comfortably above 40 tok/s on your hardware'
              : model.fit === 'good'      ? 'Meets the 20 tok/s target'
              : model.fit === 'acceptable'? 'Runnable but below the 20 tok/s comfort target'
              : model.fit === 'slow'      ? 'Runnable but slow (< 10 tok/s) — long waits'
              : 'Not runnable on this hardware (insufficient RAM)'
            }
          >
            ~{model.tok_per_s_est} tok/s
          </span>
        )}
        <a
          href={model.hf_url}
          target="_blank"
          rel="noreferrer"
          onClick={e => e.stopPropagation()}
          className="flex items-center gap-0.5 text-[var(--accent)] hover:underline"
        >
          HuggingFace <ExternalLink className="w-2.5 h-2.5" />
        </a>
      </div>

      {/* Tags */}
      <div className="flex flex-wrap gap-1 mb-2.5">
        {model.tags.map(t => (
          <span key={t} className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--bg-secondary)] text-[var(--text-muted)] border border-[var(--border)]">
            {t}
          </span>
        ))}
      </div>

      {/* Download button / progress */}
      {!isReady && (
        <div onClick={e => e.stopPropagation()}>
          <AnimatePresence mode="wait">
            {downloadState === 'idle' && (
              <motion.button
                key="btn"
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                onClick={handleDownload}
                disabled={!canRun}
                className="w-full flex items-center justify-center gap-2 py-1.5 rounded-lg text-xs font-medium
                  border border-[var(--accent)] text-[var(--accent)] bg-[var(--accent-dim)]
                  hover:bg-[var(--accent)] hover:text-white transition-all duration-200
                  disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Download className="w-3.5 h-3.5" />
                Download ({model.size_gb} GB)
              </motion.button>
            )}
            {downloadState === 'downloading' && (
              <motion.div key="progress" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="space-y-1.5">
                <div className="flex items-center justify-between text-[11px]">
                  <span className="text-[var(--text-muted)] truncate">{statusText}</span>
                  <button onClick={handleDownload} className="text-red-400 hover:text-red-300 ml-2">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
                <div className="h-1.5 rounded-full bg-[var(--bg-secondary)] overflow-hidden">
                  <motion.div
                    className="h-full rounded-full bg-[var(--accent)]"
                    animate={{ width: `${progress}%` }}
                    transition={{ duration: 0.3 }}
                  />
                </div>
                <p className="text-[10px] text-[var(--text-muted)] text-right font-mono">{progress}%</p>
              </motion.div>
            )}
            {downloadState === 'error' && (
              <motion.div key="err" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                className="flex items-center gap-2 text-[11px] text-red-400 py-1">
                <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
                <span className="truncate">{statusText}</span>
                <button onClick={handleDownload} className="ml-auto text-[var(--accent)] hover:underline whitespace-nowrap">Retry</button>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </motion.div>
  )
}
