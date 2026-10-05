import { useCallback, useEffect, useRef, useState } from 'react'
import { BrainCircuit, CheckCircle2, Download, Loader2, AlertTriangle, RotateCw, CircleDashed } from 'lucide-react'
import { clsx } from 'clsx'
import { layaStatus, startLayaDownload, type LayaStatus } from '@/lib/idp'

// Shared Laya status card — used by the setup wizard's "Decision model" step
// and Settings → Models. Shows installed / not downloaded / unavailable, and
// offers a one-time "Download Laya" button. Nothing is downloaded unless the
// user clicks that button.

const POLL_MS = 1500
const APPROX_BYTES = 846_000_000

function fmtBytes(n?: number | null): string {
  if (!n || n <= 0) return '—'
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`
  return `${Math.round(n / 1e6)} MB`
}

function deviceLabel(d?: string | null): string {
  if (!d) return 'CPU'
  if (d === 'mps') return 'Apple GPU (MPS)'
  if (d === 'cuda') return 'CUDA GPU'
  return d.toUpperCase()
}

type Phase = 'loading' | 'installed' | 'missing' | 'downloading' | 'unavailable' | 'error'

function phaseOf(s: LayaStatus | null, loading: boolean): Phase {
  if (!s) return loading ? 'loading' : 'error'
  if (s.available) return 'installed'
  if (s.downloading) return 'downloading'
  if (s.package_installed === false) return 'unavailable'
  return 'missing'
}

interface LayaStatusCardProps {
  /** Smaller layout for Settings → Models. */
  compact?: boolean
  /** Called with every status refresh (e.g. to enable the auto-router toggle). */
  onStatus?: (s: LayaStatus) => void
  className?: string
}

export function LayaStatusCard({ compact = false, onStatus, className }: LayaStatusCardProps) {
  const [status, setStatus]   = useState<LayaStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [reqError, setReqError] = useState('')
  const [starting, setStarting] = useState(false)
  const timer = useRef<number | null>(null)
  // False after unmount, so an in-flight refresh can't re-arm the poll.
  const alive = useRef(true)
  const onStatusRef = useRef(onStatus)
  onStatusRef.current = onStatus

  const refresh = useCallback(async (): Promise<LayaStatus | null> => {
    try {
      const s = await layaStatus()
      setStatus(s)
      onStatusRef.current?.(s)
      setReqError('')
      return s
    } catch (exc) {
      setReqError(exc instanceof Error ? exc.message : String(exc))
      return null
    } finally {
      setLoading(false)
    }
  }, [])

  const poll = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(async () => {
      timer.current = null
      const s = await refresh()
      if (alive.current && s?.downloading) poll()
    }, POLL_MS)
  }, [refresh])

  useEffect(() => {
    alive.current = true
    refresh().then(s => { if (alive.current && s?.downloading) poll() })
    return () => {
      alive.current = false
      if (timer.current !== null) window.clearTimeout(timer.current)
      timer.current = null
    }
  }, [refresh, poll])

  async function download() {
    setStarting(true)
    setReqError('')
    try {
      const res = await startLayaDownload()
      setStatus(res)
      onStatusRef.current?.(res)
      if (alive.current && (res.state === 'started' || res.state === 'running' || res.downloading)) poll()
    } catch (exc) {
      setReqError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setStarting(false)
    }
  }

  const phase = reqError && !status ? 'error' : phaseOf(status, loading)
  const size = status?.size_bytes ?? APPROX_BYTES
  const sizeLabel = `${status?.size_is_estimate === false ? '' : '~'}${fmtBytes(size)}`
  const got = status?.downloaded_bytes ?? 0
  const pct = phase === 'downloading' && got > 0 ? Math.min(99, Math.round((got / size) * 100)) : null
  const lastError = status?.error || reqError

  const badge = {
    loading:     { text: 'Checking…',                         cls: 'text-[var(--text-muted)] border-[var(--border)]',             Icon: Loader2 },
    installed:   { text: 'Installed · ready',                 cls: 'text-emerald-500 border-emerald-500/40 bg-emerald-500/10',     Icon: CheckCircle2 },
    missing:     { text: 'Not downloaded',                    cls: 'text-[var(--text-secondary)] border-[var(--border-bright)]',   Icon: CircleDashed },
    downloading: { text: 'Downloading…',                      cls: 'text-[var(--accent)] border-[var(--accent)]/50 bg-[var(--accent-dim)]', Icon: Loader2 },
    unavailable: { text: 'Unavailable — laya package missing', cls: 'text-amber-500 border-amber-500/40 bg-amber-500/10',          Icon: AlertTriangle },
    error:       { text: 'Status unavailable',                cls: 'text-amber-500 border-amber-500/40 bg-amber-500/10',          Icon: AlertTriangle },
  }[phase]

  return (
    <div
      data-testid="laya-status-card"
      data-phase={phase}
      className={clsx('rounded-xl glass-card border border-[var(--glass-stroke)]', compact ? 'p-3.5' : 'p-4', className)}
    >
      <div className="flex items-start gap-3">
        <div className={clsx('rounded-lg bg-[var(--accent-dim)] flex items-center justify-center flex-shrink-0',
          compact ? 'w-8 h-8' : 'w-10 h-10')}>
          <BrainCircuit className={clsx('text-[var(--accent)]', compact ? 'w-4 h-4' : 'w-5 h-5')} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-[var(--text-primary)]">Laya decision model</span>
            <span className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10.5px] font-medium', badge.cls)}>
              <badge.Icon className={clsx('w-3 h-3', (phase === 'loading' || phase === 'downloading') && 'animate-spin')} />
              {badge.text}
            </span>
          </div>
          <div className="mt-1 text-[11.5px] text-[var(--text-muted)] font-mono flex flex-wrap gap-x-2 gap-y-0.5">
            <span>{status?.params ?? '~421M'} params</span>
            <span>·</span>
            <span>{deviceLabel(status?.target_device ?? status?.device)}</span>
            <span>·</span>
            <span>{phase === 'installed' ? `${sizeLabel} on disk` : `${sizeLabel} download`}</span>
            {status?.loaded && <><span>·</span><span>loaded in memory</span></>}
          </div>
        </div>
      </div>

      {phase === 'missing' && (
        <div className="mt-3 space-y-2">
          <button
            type="button"
            onClick={download}
            disabled={starting}
            data-testid="laya-download"
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-semibold
              bg-[var(--accent)] text-white hover:bg-[var(--accent-hover)] transition-all active:scale-95 disabled:opacity-60"
          >
            {starting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
            Download Laya ({sizeLabel})
          </button>
          <p className="text-[11px] text-[var(--text-muted)] leading-relaxed">
            One-time download from Hugging Face (<span className="font-mono">{status?.repo_id ?? 'convaiinnovations/laya'}</span>,
            English weights only). It only starts when you click — nothing is fetched otherwise, and after that
            Laya runs fully offline.
          </p>
        </div>
      )}

      {phase === 'downloading' && (
        <div className="mt-3 space-y-1.5">
          <div className="h-1.5 rounded-full bg-[var(--glass-fill-hover)] overflow-hidden">
            {pct !== null ? (
              <div className="h-full bg-[var(--accent)] transition-all duration-500" style={{ width: `${pct}%` }} />
            ) : (
              <div className="h-full w-1/3 bg-[var(--accent)] animate-pulse" />
            )}
          </div>
          <p className="text-[11px] text-[var(--text-muted)]">
            Downloading from Hugging Face{pct !== null ? ` — ${fmtBytes(got)} of ${sizeLabel} (${pct}%)` : ` (${sizeLabel})`}.
            You can continue — it keeps running in the background.
          </p>
        </div>
      )}

      {phase === 'unavailable' && (
        <p className="mt-3 text-[11px] text-[var(--text-muted)] leading-relaxed">
          The <span className="font-mono">laya</span> Python package isn't installed in Persephone's Python
          (<span className="font-mono">pip install "laya&gt;=0.3.20"</span>, or <span className="font-mono">npm run setup</span>).
          Keyword rules are used meanwhile.
        </p>
      )}

      {lastError && phase !== 'installed' && phase !== 'downloading' && phase !== 'unavailable' && (
        <div className="mt-2 flex items-start gap-2 text-[11px] text-red-500">
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-px" />
          <span className="flex-1 break-words">{lastError}</span>
          <button
            type="button"
            onClick={phase === 'error' ? () => { setLoading(true); refresh() } : download}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent-dim)]"
          >
            <RotateCw className="w-3 h-3" /> Retry
          </button>
        </div>
      )}
    </div>
  )
}
