import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import {
  Bot, Play, Loader2, RefreshCw, CheckCircle2, AlertTriangle, Clock, Pause, Zap,
  ArrowUpRight, X,
} from 'lucide-react'
import { Panel } from '@/components/ui/Panel'
import type { DelegatedTask } from '@/types'
import { useAppStore } from '@/store/appStore'

interface WorkerState {
  id:                 string
  name:               string
  description:        string
  interval_seconds:   number
  enabled:            boolean
  last_run_ts:        number
  last_duration_s:    number
  last_result:        Record<string, unknown>
  last_error:         string
  next_due_ts:        number
  seconds_until_due:  number
}

interface StatusResponse {
  user_idle:                 boolean
  seconds_since_last_active: number
  idle_threshold_seconds:    number
  workers:                   WorkerState[]
}

interface LogEvent {
  ts:      number
  worker:  string
  level:   'info' | 'warning' | 'error' | 'debug'
  message: string
  result?: Record<string, unknown>
}

function fmtSecondsAgo(tsSeconds: number): string {
  if (!tsSeconds) return 'never'
  const delta = Date.now() / 1000 - tsSeconds
  if (delta < 60)   return `${Math.round(delta)}s ago`
  if (delta < 3600) return `${Math.round(delta / 60)}m ago`
  if (delta < 86400) return `${Math.round(delta / 3600)}h ago`
  return `${Math.round(delta / 86400)}d ago`
}

function fmtDuration(seconds: number): string {
  if (seconds < 60)  return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${Math.round(seconds / 3600)}h`
}

export function WorkersView() {
  const [status,  setStatus]  = useState<StatusResponse | null>(null)
  const [events,  setEvents]  = useState<LogEvent[]>([])
  const [running, setRunning] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [sRes, lRes] = await Promise.all([
        fetch('/api/workers/status'),
        fetch('/api/workers/logs?limit=80'),
      ])
      const s = await sRes.json() as StatusResponse
      const l = await lRes.json() as { events: LogEvent[] }
      setStatus(s)
      setEvents(l.events ?? [])
    } catch { /* silent */ }
  }, [])

  useEffect(() => {
    void refresh()
    // Poll every 3s while the tab is visible — cheap, and worker state
    // changes on schedule ticks (every 5s server-side).
    pollRef.current = window.setInterval(() => { void refresh() }, 3000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [refresh])

  async function toggleWorker(id: string, next: boolean) {
    try {
      await fetch(`/api/workers/${id}/enable`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      })
      await refresh()
    } catch { /* silent */ }
  }

  async function runNow(id: string) {
    if (running) return
    setRunning(id)
    try {
      await fetch(`/api/workers/${id}/run-now`, { method: 'POST' })
    } catch { /* silent */ }
    finally { setRunning(null); void refresh() }
  }

  const idleBadge = status?.user_idle
    ? { text: 'idle — workers can run', tone: 'text-emerald-300 border-emerald-500/40 bg-emerald-500/10' }
    : { text: `active — waiting (${Math.max(0, Math.round((status?.idle_threshold_seconds ?? 60) - (status?.seconds_since_last_active ?? 0)))}s until idle)`,
        tone: 'text-amber-300 border-amber-500/40 bg-amber-500/10' }

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto p-6 space-y-5">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="font-serif text-2xl text-[var(--text-primary)] flex items-center gap-2">
              <Bot className="w-5 h-5 text-[var(--accent)]" /> Background Workers
            </h1>
            <p className="text-sm text-[var(--text-muted)] mt-1">
              Idle-time swarm — workers run only when you've been quiet for a bit, one at
              a time, so they never fight the active chat model for memory.
            </p>
          </div>
          <button
            onClick={() => void refresh()}
            className="p-2 rounded-lg border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)]/20 transition-colors"
            title="Refresh"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>

        {/* Idle-state banner */}
        {status && (
          <div className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-[11px] font-mono ${idleBadge.tone}`}>
            <Clock className="w-3.5 h-3.5 shrink-0" />
            {idleBadge.text}
          </div>
        )}

        {/* Worker cards */}
        <div className="space-y-3">
          {(status?.workers ?? []).map(w => {
            const isRunning = running === w.id
            const hasError  = !!w.last_error
            const hasRun    = w.last_run_ts > 0
            const dueIn     = Math.max(0, Math.round(w.seconds_until_due))
            const resultSummary = summariseResult(w.id, w.last_result)
            return (
              <motion.div
                key={w.id}
                initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                className="rounded-xl border border-[var(--border)] bg-[var(--bg-primary)]/40 p-4 space-y-3"
              >
                <div className="flex items-start gap-3">
                  <div className={`w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 ${
                    w.enabled ? 'bg-[var(--accent-dim)] text-[var(--accent)]' : 'bg-[var(--bg-tertiary)] text-[var(--text-muted)]'
                  }`}>
                    <Bot className="w-4 h-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <div className="text-[15px] text-[var(--text-primary)] leading-none">{w.name}</div>
                      <span className="text-[9.5px] font-mono uppercase tracking-wider text-[var(--text-muted)]">
                        every {fmtDuration(w.interval_seconds)}
                      </span>
                    </div>
                    <div className="text-[12px] text-[var(--text-muted)] mt-1.5 leading-relaxed">
                      {w.description}
                    </div>
                  </div>
                  {/* Enable/disable */}
                  <label className="flex-shrink-0 cursor-pointer select-none">
                    <input type="checkbox" checked={w.enabled} onChange={e => toggleWorker(w.id, e.target.checked)} className="sr-only" />
                    <span className={`relative block w-9 h-5 rounded-full transition-colors ${
                      w.enabled ? 'bg-[var(--accent)]' : 'bg-[var(--bg-tertiary)]'
                    }`}>
                      <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                        w.enabled ? 'translate-x-4' : ''
                      }`} />
                    </span>
                  </label>
                </div>

                {/* Status row */}
                <div className="flex flex-wrap items-center gap-2 text-[10.5px] font-mono">
                  <span className={`flex items-center gap-1 px-2 py-0.5 rounded border ${
                    hasError
                      ? 'border-red-500/40 bg-red-500/10 text-red-300'
                      : hasRun
                        ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                        : 'border-[var(--border)] text-[var(--text-muted)]'
                  }`}>
                    {hasError ? <AlertTriangle className="w-3 h-3" /> : hasRun ? <CheckCircle2 className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
                    {hasError ? 'last run failed' : hasRun ? `ran ${fmtSecondsAgo(w.last_run_ts)}` : 'never run'}
                  </span>
                  {hasRun && !hasError && (
                    <span className="px-2 py-0.5 rounded border border-[var(--border)] text-[var(--text-muted)]">
                      took {fmtDuration(w.last_duration_s)}
                    </span>
                  )}
                  {w.enabled && !isRunning && (
                    <span className="px-2 py-0.5 rounded border border-[var(--border)] text-[var(--text-muted)]">
                      next {dueIn === 0 ? 'ready' : `in ${fmtDuration(dueIn)}`}
                    </span>
                  )}
                  {!w.enabled && (
                    <span className="px-2 py-0.5 rounded border border-[var(--border)] text-[var(--text-muted)]">
                      disabled
                    </span>
                  )}
                  {resultSummary && !hasError && (
                    <span className="text-[var(--text-muted)] italic">
                      → {resultSummary}
                    </span>
                  )}
                  {hasError && (
                    <span className="text-red-300 italic truncate max-w-md" title={w.last_error}>
                      → {w.last_error}
                    </span>
                  )}
                  <button
                    onClick={() => runNow(w.id)}
                    disabled={isRunning}
                    className="ml-auto flex items-center gap-1 px-2.5 py-1 rounded border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--accent)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)]/20 transition-colors disabled:opacity-50"
                  >
                    {isRunning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                    run now
                  </button>
                </div>
              </motion.div>
            )
          })}
          {!status && (
            <div className="text-center py-8 text-[var(--text-muted)] text-sm">
              <Loader2 className="w-4 h-4 animate-spin inline mr-2" />
              loading workers…
            </div>
          )}
        </div>

        {/* Delegated tasks */}
        <DelegatedTasksPanel />

        {/* Activity log */}
        <Panel className="p-4 space-y-3">
          <div className="flex items-center gap-2">
            <Zap className="w-3.5 h-3.5 text-[var(--accent)]" />
            <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Recent activity</span>
            <div className="ml-auto text-[10px] font-mono text-[var(--text-muted)]">
              {events.length} event{events.length === 1 ? '' : 's'}
            </div>
          </div>
          {events.length === 0 ? (
            <div className="text-[11.5px] text-[var(--text-muted)] italic py-4 text-center">
              No activity yet — workers will start once you've been idle for {status?.idle_threshold_seconds ?? 60}s.
            </div>
          ) : (
            <div className="space-y-1 font-mono text-[11px] max-h-72 overflow-y-auto">
              {[...events].reverse().map((e, i) => (
                <div key={i} className="flex items-baseline gap-2 py-0.5">
                  <span className="text-[var(--text-muted)] whitespace-nowrap w-14">
                    {fmtSecondsAgo(e.ts)}
                  </span>
                  <span className={`w-14 truncate ${
                    e.level === 'error' ? 'text-red-300'
                    : e.level === 'warning' ? 'text-amber-300'
                    : 'text-[var(--accent)]'
                  }`}>
                    {e.worker}
                  </span>
                  <span className="text-[var(--text-primary)] truncate">{e.message}</span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
    </div>
  )
}

/**
 * Turn a worker's raw last_result dict into a short human phrase.
 * Kept per-worker on purpose — the dict shape differs between workers.
 */
function summariseResult(workerId: string, r: Record<string, unknown> | undefined): string {
  if (!r) return ''
  if (workerId === 'memory_curator') {
    const dedup = r.dedup as { scanned?: number, removed?: number } | undefined
    const reex  = r.reextract as { conversations?: number, new_facts?: number } | undefined
    const parts: string[] = []
    if (dedup) parts.push(`${dedup.removed ?? 0} dupes removed`)
    if (reex)  parts.push(`${reex.new_facts ?? 0} new facts from ${reex.conversations ?? 0} convos`)
    return parts.join(', ')
  }
  if (workerId === 'model_warmer') {
    if (r.skipped) return String(r.skipped)
    if (r.ok === false) return `warm failed on ${r.model ?? '?'} — ${r.error ?? ''}`
    if (r.ok === true)  return `${r.model} kept warm (${r.latency_ms ?? '?'}ms)`
  }
  return ''
}


// ───────────────────────────────────────────────────────────────────────────
// Delegated tasks panel
// ───────────────────────────────────────────────────────────────────────────
// Shows recent + in-progress delegated tasks across all conversations. Not
// gated by the idle scheduler — delegates run whenever the main model asks,
// independent of user-idle status. Cancel-in-progress supported.
function DelegatedTasksPanel() {
  const [tasks,   setTasks]   = useState<DelegatedTask[]>([])
  const [loading, setLoading] = useState(true)
  const [cancelling, setCancelling] = useState<string | null>(null)
  const activeConvId = useAppStore(s => s.activeConversationId)

  const refresh = useCallback(async () => {
    try {
      const r = await fetch('/api/delegate/tasks?limit=30')
      const d = await r.json() as { tasks: DelegatedTask[] }
      setTasks(d.tasks ?? [])
    } catch { /* silent */ }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    void refresh()
    // Poll every 2.5s while the tab is open — cheap and matches the chat
    // window's delegate-poll cadence so state stays in sync.
    const h = window.setInterval(() => { void refresh() }, 2500)
    return () => clearInterval(h)
  }, [refresh])

  async function cancelTask(id: string) {
    if (cancelling) return
    setCancelling(id)
    try {
      await fetch(`/api/delegate/${id}/cancel`, { method: 'POST' })
      await refresh()
    } catch { /* silent */ }
    finally { setCancelling(null) }
  }

  const inflight = tasks.filter(t => t.status === 'pending' || t.status === 'running')
  const recent   = tasks.filter(t => t.status !== 'pending' && t.status !== 'running').slice(0, 10)

  return (
    <Panel className="p-4 space-y-3">
      <div className="flex items-center gap-2">
        <ArrowUpRight className="w-3.5 h-3.5 text-amber-300" />
        <span className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Auxiliary workers</span>
        <div className="ml-auto text-[10px] font-mono text-[var(--text-muted)]">
          {loading ? 'loading…'
           : inflight.length > 0
             ? <><span className="text-amber-300">{inflight.length} in flight</span>{recent.length > 0 ? ` · ${recent.length} recent` : ''}</>
             : `${recent.length} recent`}
        </div>
      </div>

      {/* In-progress first */}
      {inflight.length > 0 && (
        <div className="space-y-1.5">
          {inflight.map(t => (
            <DelegateTaskRow
              key={t.id} task={t}
              isActiveConv={t.conversation_id === activeConvId}
              onCancel={cancelTask}
              cancelling={cancelling === t.id}
            />
          ))}
        </div>
      )}

      {/* Recent */}
      {recent.length > 0 && (
        <div className="space-y-1.5">
          {recent.map(t => (
            <DelegateTaskRow
              key={t.id} task={t}
              isActiveConv={t.conversation_id === activeConvId}
              onCancel={cancelTask}
              cancelling={cancelling === t.id}
            />
          ))}
        </div>
      )}

      {!loading && tasks.length === 0 && (
        <div className="text-[11.5px] text-[var(--text-muted)] italic py-4 text-center">
          No auxiliary workers running. Use the amber Send-to-Worker button
          next to Send in the main chat input to hand a task to a specialist
          model — the judge picks the best fit and it runs in the background.
        </div>
      )}
    </Panel>
  )
}


function DelegateTaskRow({
  task, isActiveConv, onCancel, cancelling,
}: {
  task:          DelegatedTask
  isActiveConv:  boolean
  onCancel:      (id: string) => void
  cancelling:    boolean
}) {
  const [open, setOpen] = useState(false)
  const running = task.status === 'pending' || task.status === 'running'
  const failed  = task.status === 'failed'
  const cancelled = task.status === 'cancelled'
  const done    = task.status === 'done'

  const statusChip = running
    ? { icon: <Loader2 className="w-3 h-3 animate-spin" />, tone: 'text-amber-300', label: task.status }
    : done
      ? { icon: <CheckCircle2 className="w-3 h-3" />, tone: 'text-emerald-300', label: 'done' }
      : failed
        ? { icon: <AlertTriangle className="w-3 h-3" />, tone: 'text-red-300', label: 'failed' }
        : { icon: <X className="w-3 h-3" />, tone: 'text-[var(--text-muted)]', label: 'cancelled' }

  const duration = task.completed_at && task.started_at
    ? Math.round(task.completed_at - task.started_at)
    : task.started_at
      ? Math.round(Date.now() / 1000 - task.started_at)
      : 0

  return (
    <div className={`rounded-lg border p-2.5 ${
      running ? 'border-amber-400/40 bg-amber-400/5'
      : failed ? 'border-red-500/30 bg-red-500/5'
      : 'border-[var(--border)] bg-[var(--bg-primary)]/40'
    }`}>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 text-left"
      >
        <span className={`flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider ${statusChip.tone}`}>
          {statusChip.icon}{statusChip.label}
        </span>
        <span className="text-[10px] font-mono text-[var(--text-muted)] uppercase">
          {task.category}
        </span>
        <span className="text-[10px] font-mono text-[var(--accent)]">
          → {task.delegate_model.split(':')[0]}
        </span>
        <span className="text-[13px] text-[var(--text-primary)] truncate flex-1">
          {task.prompt}
        </span>
        {duration > 0 && (
          <span className="text-[10px] font-mono text-[var(--text-muted)]">
            {duration < 60 ? `${duration}s` : `${Math.round(duration / 60)}m`}
          </span>
        )}
        {isActiveConv && (
          <span className="text-[9.5px] font-mono text-[var(--accent)] uppercase tracking-wider">
            in view
          </span>
        )}
        {running && (
          <button
            onClick={ev => { ev.stopPropagation(); onCancel(task.id) }}
            disabled={cancelling}
            className="p-1 rounded text-[var(--text-muted)] hover:text-red-300 hover:bg-red-500/10 disabled:opacity-50"
            title="Cancel this task"
          >
            {cancelling ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <X className="w-3.5 h-3.5" />}
          </button>
        )}
      </button>
      {open && (
        <div className="mt-2 pt-2 border-t border-[var(--border)] space-y-2 text-[11px] font-mono">
          <div className="text-[var(--text-muted)]">Prompt</div>
          <div className="text-[var(--text-primary)] whitespace-pre-wrap">{task.prompt}</div>
          {task.result && (
            <>
              <div className="text-[var(--text-muted)] mt-2">Worker reply</div>
              <div className="text-[var(--text-primary)] whitespace-pre-wrap max-h-40 overflow-y-auto">{task.result}</div>
            </>
          )}
          {task.comment && (
            <>
              <div className="text-[var(--text-muted)] mt-2">Main-model comment</div>
              <div className="text-[var(--text-primary)] whitespace-pre-wrap">{task.comment}</div>
            </>
          )}
          {task.error && (
            <>
              <div className="text-red-300 mt-2">Error</div>
              <div className="text-red-300 whitespace-pre-wrap">{task.error}</div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
