import { useEffect, useRef, useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  CheckCircle, AlertCircle, Download, Play, ExternalLink,
  Terminal, RefreshCw, Server, Apple, Monitor,
} from 'lucide-react'

interface OllamaStatus {
  installed: boolean
  running: boolean
  version: string
  executable: string | null
  install_info: {
    method: 'shell' | 'download' | 'unknown'
    command: string
    url: string
    label: string
    instructions: string
    requires_manual: boolean
  }
  os: 'Darwin' | 'Linux' | 'Windows' | string
}

interface OllamaStepProps {
  onReady: () => void   // called when Ollama is running
}

const OS_ICON: Record<string, React.ElementType> = {
  Darwin: Apple, Linux: Server, Windows: Monitor,
}

const OS_LABEL: Record<string, string> = {
  Darwin: 'macOS', Linux: 'Linux', Windows: 'Windows',
}

export function OllamaStep({ onReady }: OllamaStepProps) {
  const [status, setStatus]   = useState<OllamaStatus | null>(null)
  const [busy, setBusy]       = useState(false)
  const [logs, setLogs]       = useState<string[]>([])
  const [error, setError]     = useState<string | null>(null)
  const pollingRef = useRef<number | null>(null)
  const fired = useRef(false)
  const logsRef = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/setup/ollama')
      const data = await r.json()
      setStatus(data)
      if (data.running && !fired.current) {
        fired.current = true
      }
    } catch {
      setError('Failed to query backend')
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  // Auto-poll every 2s when waiting for install or start
  useEffect(() => {
    if (status?.running) {
      if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null }
      return
    }
    pollingRef.current = window.setInterval(refresh, 2000)
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [status?.running, refresh])

  // Auto-scroll logs to bottom
  useEffect(() => {
    if (logsRef.current) logsRef.current.scrollTop = logsRef.current.scrollHeight
  }, [logs])

  async function handleInstall() {
    if (!status) return
    if (status.install_info.requires_manual) {
      // Windows: open download page in a new tab
      window.open(status.install_info.url, '_blank', 'noopener')
      setLogs(prev => [...prev,
        `Opened ${status.install_info.url}`,
        '',
        'Please:',
        '  1. Download and run the installer',
        '  2. Approve the Windows UAC prompt',
        '  3. Once installed, this wizard will detect Ollama automatically',
        '',
        'Polling every 2 seconds…',
      ])
      return
    }

    setBusy(true)
    setLogs(['Starting installation…'])
    setError(null)

    try {
      const res = await fetch('/api/setup/ollama/install', { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const reader = res.body!.getReader()
      const dec = new TextDecoder()
      let buf = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6).trim()
          if (payload === '[DONE]') {
            await refresh()
            setBusy(false)
            return
          }
          try {
            const obj = JSON.parse(payload)
            if (obj.line !== undefined) setLogs(p => [...p, obj.line])
            if (obj.running) await refresh()
          } catch { /* ignore */ }
        }
      }
    } catch (e: any) {
      setError(e.message ?? 'Install failed')
    } finally {
      setBusy(false)
    }
  }

  async function handleStart() {
    setBusy(true)
    setError(null)
    try {
      const r = await fetch('/api/setup/ollama/start', { method: 'POST' })
      const data = await r.json()
      if (!data.ok) setError(data.error || 'Failed to start Ollama')
      await refresh()
    } catch (e: any) {
      setError(e.message ?? 'Failed to start')
    } finally {
      setBusy(false)
    }
  }

  if (!status) {
    return (
      <div className="max-w-md mx-auto text-center">
        <Loader />
        <p className="text-xs text-[var(--text-muted)] mt-3">Checking Ollama…</p>
      </div>
    )
  }

  const OSIcon = OS_ICON[status.os] ?? Server

  // ── READY ────────────────────────────────────────────────────────────────
  if (status.running) {
    // notify parent (but only once)
    if (!fired.current) { fired.current = true; setTimeout(onReady, 0) }
    return (
      <div className="max-w-md mx-auto text-center space-y-5">
        <motion.div
          initial={{ scale: 0 }} animate={{ scale: 1 }}
          transition={{ type: 'spring', stiffness: 200, damping: 14 }}
          className="w-20 h-20 mx-auto rounded-full bg-gradient-to-br from-green-500 to-emerald-600 flex items-center justify-center shadow-xl shadow-green-500/40"
        >
          <CheckCircle className="w-10 h-10 text-white" />
        </motion.div>
        <div>
          <h2 className="font-serif text-2xl text-[var(--text-primary)]">Ollama is ready</h2>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Detected version <span className="font-mono text-[var(--accent)]">{status.version || 'unknown'}</span>
          </p>
        </div>
        <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-[var(--bg-tertiary)] border border-[var(--border)]">
          <OSIcon className="w-3.5 h-3.5 text-[var(--text-muted)]" />
          <span className="text-xs text-[var(--text-secondary)]">{OS_LABEL[status.os] ?? status.os}</span>
          {status.executable && (
            <span className="text-[10px] text-[var(--text-muted)] font-mono ml-1 truncate max-w-48">
              {status.executable}
            </span>
          )}
        </div>
      </div>
    )
  }

  // ── INSTALLED BUT NOT RUNNING ────────────────────────────────────────────
  if (status.installed) {
    return (
      <div className="max-w-md mx-auto space-y-5 text-center">
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }}
          className="w-20 h-20 mx-auto rounded-full bg-[var(--gold-dim)] flex items-center justify-center"
        >
          <Server className="w-10 h-10 text-[var(--gold)]" />
        </motion.div>
        <div>
          <h2 className="font-serif text-2xl text-[var(--text-primary)]">Ollama installed</h2>
          <p className="text-sm text-[var(--text-muted)] mt-1">It's installed but not running yet.</p>
        </div>

        <button
          onClick={handleStart}
          disabled={busy}
          className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl
            bg-[var(--accent)] text-white text-sm font-semibold
            hover:bg-[var(--accent-hover)] shadow-lg shadow-[var(--accent-glow)]
            transition-all disabled:opacity-60"
        >
          {busy ? <Loader small /> : <Play className="w-4 h-4 fill-current" />}
          {busy ? 'Starting…' : 'Start Ollama'}
        </button>

        {error && <ErrorBox text={error} />}

        <p className="text-xs text-[var(--text-muted)]">
          Auto-detecting every 2 seconds…
        </p>
      </div>
    )
  }

  // ── NOT INSTALLED ────────────────────────────────────────────────────────
  return (
    <div className="max-w-lg mx-auto space-y-5">
      <div className="flex flex-col items-center text-center gap-2.5">
        <motion.div
          animate={{ scale: [1, 1.04, 1] }} transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
          className="w-20 h-20 rounded-full bg-[var(--bg-tertiary)] border-2 border-dashed border-[var(--border-bright)] flex items-center justify-center"
        >
          <Server className="w-9 h-9 text-[var(--accent)]" />
        </motion.div>
        <h2 className="font-serif text-2xl text-[var(--text-primary)]">Install Ollama</h2>
        <p className="text-sm text-[var(--text-muted)] max-w-md">
          Persephone needs Ollama to run local models. It's free, open-source, and runs entirely on your machine.
        </p>
      </div>

      {/* OS badge */}
      <div className="flex items-center justify-center gap-2 text-xs text-[var(--text-secondary)]">
        <OSIcon className="w-4 h-4" />
        Detected: <strong>{OS_LABEL[status.os] ?? status.os}</strong>
      </div>

      {/* Install card */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-4 space-y-3">
        <p className="text-xs text-[var(--text-muted)] leading-relaxed">
          {status.install_info.instructions}
        </p>

        {status.install_info.method === 'shell' && status.install_info.command && (
          <div className="rounded-lg bg-black/40 border border-[var(--border)] px-3 py-2">
            <code className="text-[11px] font-mono text-[var(--accent)] break-all">
              {status.install_info.command}
            </code>
          </div>
        )}

        <button
          onClick={handleInstall}
          disabled={busy}
          className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl
            bg-[var(--accent)] text-white text-sm font-semibold
            hover:bg-[var(--accent-hover)] shadow-lg shadow-[var(--accent-glow)]
            transition-all disabled:opacity-60"
        >
          {busy ? (
            <>
              <Loader small />
              Installing…
            </>
          ) : status.install_info.requires_manual ? (
            <>
              <ExternalLink className="w-4 h-4" />
              {status.install_info.label}
            </>
          ) : (
            <>
              <Download className="w-4 h-4" />
              {status.install_info.label}
            </>
          )}
        </button>

        {/* Log terminal */}
        <AnimatePresence>
          {logs.length > 0 && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden"
            >
              <div className="flex items-center gap-1.5 mt-1 mb-1.5">
                <Terminal className="w-3 h-3 text-[var(--text-muted)]" />
                <span className="text-[10px] text-[var(--text-muted)] uppercase tracking-wide">Output</span>
              </div>
              <div
                ref={logsRef}
                className="rounded-lg bg-black/60 border border-[var(--border)] p-2.5 max-h-48 overflow-y-auto"
                style={{ scrollbarWidth: 'thin' }}
              >
                <pre className="text-[10px] font-mono text-[var(--text-secondary)] whitespace-pre-wrap leading-snug">
                  {logs.join('\n')}
                </pre>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {error && <ErrorBox text={error} />}
      </div>

      {/* Manual fallback */}
      <div className="text-center">
        <button
          onClick={refresh}
          className="inline-flex items-center gap-1 text-[11px] text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
        >
          <RefreshCw className="w-3 h-3" />
          Already installed? Re-check
        </button>
      </div>
    </div>
  )
}

// ── helpers ──────────────────────────────────────────────────────────────────
function Loader({ small = false }: { small?: boolean }) {
  return (
    <motion.div
      className={small ? "w-4 h-4 rounded-full border-2 border-white/30 border-t-white" : "w-6 h-6 rounded-full border-2 border-[var(--accent)] border-t-transparent"}
      animate={{ rotate: 360 }}
      transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }}
    />
  )
}

function ErrorBox({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2 p-2.5 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-xs">
      <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
      <span>{text}</span>
    </div>
  )
}
