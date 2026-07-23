import { useCallback, useEffect, useState } from 'react'
import {
  Sparkles, RefreshCw, Zap, CheckCircle2, AlertTriangle, Loader2,
} from 'lucide-react'
import { useAppStore } from '@/store/appStore'

interface ParallelConfig {
  num_parallel:         number
  max_loaded:           number
  num_parallel_target:  number
  max_loaded_target:    number
  os:                   string
  ok:                   boolean
  hint:                 string
}

export function SetupSection() {
  const { setWizardCompleted } = useAppStore()
  const [confirming, setConfirming] = useState(false)

  // Ollama parallelism state
  const [parCfg, setParCfg]         = useState<ParallelConfig | null>(null)
  const [parLoading, setParLoading] = useState(true)
  const [parApplying, setParApplying] = useState(false)
  const [parMessage, setParMessage] = useState<string>('')
  const [parError,   setParError]   = useState<string>('')

  const loadParallel = useCallback(async () => {
    try {
      const r = await fetch('/api/setup/ollama-parallel')
      if (r.ok) setParCfg(await r.json())
    } catch { /* silent */ }
    finally { setParLoading(false) }
  }, [])
  useEffect(() => { void loadParallel() }, [loadParallel])

  async function applyParallel() {
    if (!parCfg || parApplying) return
    setParApplying(true)
    setParMessage(''); setParError('')
    try {
      const r = await fetch('/api/setup/ollama-parallel', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          num_parallel: parCfg.num_parallel_target,
          max_loaded:   parCfg.max_loaded_target,
        }),
      })
      const d = await r.json() as { ok?: boolean; error?: string; hint?: string; config?: ParallelConfig }
      if (d.config) setParCfg(d.config)
      if (d.ok) setParMessage(d.hint || 'Parallelism enabled.')
      else       setParError(d.error || 'Failed to enable parallelism.')
    } catch (exc) {
      setParError(exc instanceof Error ? exc.message : String(exc))
    } finally { setParApplying(false) }
  }

  function rerun() {
    setWizardCompleted(false)
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h3 className="font-serif text-xl text-[var(--text-primary)] mb-1">Setup Wizard</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Run the initial setup again to reconfigure models, voice, tools, and theme.
          Your existing settings stay in place until you finish the wizard and press Launch.
        </p>
      </div>

      {/* Parallel-tab throughput */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-5">
        <div className="flex items-start gap-3">
          <div className={`w-9 h-9 rounded-lg border flex items-center justify-center flex-shrink-0 ${
            parCfg?.ok
              ? 'bg-emerald-500/10 border-emerald-500/40 text-emerald-300'
              : 'bg-amber-400/10 border-amber-400/40 text-amber-300'
          }`}>
            {parCfg?.ok ? <CheckCircle2 className="w-4 h-4" /> : <Zap className="w-4 h-4" />}
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-serif text-base text-[var(--text-primary)]">Parallel-tab throughput</div>
            <div className="text-xs text-[var(--text-muted)] mt-0.5">
              Ollama serialises concurrent requests unless <code>OLLAMA_NUM_PARALLEL</code> and
              <code> OLLAMA_MAX_LOADED_MODELS</code> are set. Without both, chat tabs will
              queue behind each other even though the frontend runs them in parallel.
            </div>
            {parLoading ? (
              <div className="mt-3 text-xs text-[var(--text-muted)] flex items-center gap-1.5">
                <Loader2 className="w-3 h-3 animate-spin" /> checking…
              </div>
            ) : parCfg && (
              <div className="mt-3 space-y-2 text-xs">
                <div className="flex items-center gap-3 font-mono text-[11px]">
                  <span>OLLAMA_NUM_PARALLEL = <b className={parCfg.num_parallel >= 2 ? 'text-emerald-300' : 'text-amber-300'}>
                    {parCfg.num_parallel || 'unset'}
                  </b></span>
                  <span>OLLAMA_MAX_LOADED_MODELS = <b className={parCfg.max_loaded >= 2 ? 'text-emerald-300' : 'text-amber-300'}>
                    {parCfg.max_loaded || 'unset'}
                  </b></span>
                </div>
                {parCfg.hint && (
                  <div className={`text-[11.5px] ${parCfg.ok ? 'text-emerald-300' : 'text-amber-300'} flex items-start gap-1.5`}>
                    {parCfg.ok ? <CheckCircle2 className="w-3 h-3 mt-0.5" /> : <AlertTriangle className="w-3 h-3 mt-0.5" />}
                    <span>{parCfg.hint}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
        <div className="mt-4 flex items-center gap-3">
          {!parCfg?.ok && (
            <button
              onClick={applyParallel}
              disabled={parApplying}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                bg-[var(--accent)] text-white shadow-md shadow-[var(--accent-glow)]
                hover:bg-[var(--accent-hover)] transition-all active:scale-95 disabled:opacity-50"
            >
              {parApplying ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
              {parApplying ? 'Applying + restarting Ollama…' : `Enable parallel tabs (set NUM_PARALLEL=${parCfg?.num_parallel_target ?? 4}, MAX_LOADED=${parCfg?.max_loaded_target ?? 2})`}
            </button>
          )}
          <button
            onClick={() => { void loadParallel() }}
            className="p-2 rounded-lg border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] transition-colors"
            title="Re-check current values"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
        {parMessage && (
          <div className="mt-3 text-xs text-emerald-300 flex items-center gap-1.5">
            <CheckCircle2 className="w-3 h-3" /> {parMessage}
          </div>
        )}
        {parError && (
          <div className="mt-3 text-xs text-red-300 whitespace-pre-wrap">{parError}</div>
        )}
      </div>

      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-5">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-lg bg-[var(--accent-dim)] border border-[var(--border-bright)]
            flex items-center justify-center flex-shrink-0">
            <Sparkles className="w-4 h-4 text-[var(--accent)]" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="font-serif text-base text-[var(--text-primary)]">Re-run Setup</div>
            <div className="text-xs text-[var(--text-muted)] mt-0.5">
              Walk through the guided setup wizard from scratch.
            </div>
          </div>
        </div>

        <div className="mt-5 flex items-center gap-3">
          {!confirming ? (
            <button
              onClick={() => setConfirming(true)}
              className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                bg-[var(--accent)] text-white shadow-md shadow-[var(--accent-glow)]
                hover:bg-[var(--accent-hover)] transition-all active:scale-95"
            >
              <RefreshCw className="w-4 h-4" />
              Run Setup Wizard
            </button>
          ) : (
            <>
              <button
                onClick={rerun}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
                  bg-[var(--accent)] text-white shadow-md shadow-[var(--accent-glow)]
                  hover:bg-[var(--accent-hover)] transition-all active:scale-95"
              >
                <Sparkles className="w-4 h-4" />
                Yes, start wizard
              </button>
              <button
                onClick={() => setConfirming(false)}
                className="px-4 py-2 rounded-lg text-sm font-medium
                  text-[var(--text-secondary)] hover:text-[var(--text-primary)]
                  hover:bg-[var(--bg-tertiary)] transition-all"
              >
                Cancel
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
