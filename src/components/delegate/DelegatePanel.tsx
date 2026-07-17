import { useCallback, useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Bot, Loader2, CheckCircle2, AlertTriangle, X, Wrench, Brain, Zap,
  Activity, History as HistoryIcon,
} from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import type { DelegatedTask } from '@/types'

interface TaskProgress {
  stage:       'queued' | 'picking' | 'streaming' | 'commenting' | 'done' | 'failed'
  content:     string
  thinking:    string
  tool_events: Array<{
    name:    string
    args:    Record<string, unknown>
    status:  'running' | 'done'
    preview?: string
    ts:      number
  }>
  tokens:     number
  started_at: number
  updated_at: number
}

/**
 * Right-panel Auxiliary Models tab — a live view of the worker models the
 * user dispatched via the "send to worker" button. Shows streaming content
 * + <think> tokens + tool calls in real time for every in-flight task.
 *
 * The component name / file path stays `DelegatePanel` so the underlying
 * `/api/delegate/*` endpoints and the store's `rightPanel: 'delegate'` id
 * remain stable — only user-visible labels changed to "Auxiliary Models".
 */
type PanelTab = 'live' | 'history'

export function DelegatePanel() {
  const activeConvId = useAppStore(s => s.activeConversationId)
  const [tab, setTab] = useState<PanelTab>('live')
  const [tasks, setTasks] = useState<DelegatedTask[]>([])
  const [historyTasks, setHistoryTasks] = useState<DelegatedTask[]>([])
  const [loading, setLoading] = useState(true)
  const [progress, setProgress] = useState<Record<string, TaskProgress | null>>({})

  const refreshTasks = useCallback(async () => {
    if (!activeConvId) { setTasks([]); setLoading(false); return }
    try {
      const r = await fetch(`/api/delegate/tasks?conv_id=${encodeURIComponent(activeConvId)}&limit=20`)
      const d = await r.json() as { tasks: DelegatedTask[] }
      setTasks(d.tasks ?? [])
    } catch { /* silent */ }
    finally { setLoading(false) }
  }, [activeConvId])

  const refreshHistory = useCallback(async () => {
    // Global history — all conversations, done/failed/cancelled only.
    try {
      const r = await fetch('/api/delegate/tasks?limit=100')
      const d = await r.json() as { tasks: DelegatedTask[] }
      setHistoryTasks((d.tasks ?? []).filter(
        t => t.status === 'done' || t.status === 'failed' || t.status === 'cancelled',
      ))
    } catch { /* silent */ }
  }, [])

  useEffect(() => { void refreshTasks() }, [refreshTasks])
  useEffect(() => { if (tab === 'history') void refreshHistory() }, [tab, refreshHistory])

  // Poll: task list every 2s (Live tab); history every 5s.
  useEffect(() => {
    if (tab !== 'live') return
    const h = window.setInterval(() => { void refreshTasks() }, 2000)
    return () => clearInterval(h)
  }, [tab, refreshTasks])
  useEffect(() => {
    if (tab !== 'history') return
    const h = window.setInterval(() => { void refreshHistory() }, 5000)
    return () => clearInterval(h)
  }, [tab, refreshHistory])

  const inflight = tasks.filter(t => t.status === 'pending' || t.status === 'running')
  const recentDone = tasks.filter(t => t.status === 'done' || t.status === 'failed' || t.status === 'cancelled').slice(0, 5)

  useEffect(() => {
    if (inflight.length === 0) return
    const ids = inflight.map(t => t.id)
    let cancelled = false
    async function pull() {
      if (cancelled) return
      await Promise.all(ids.map(async id => {
        try {
          const r = await fetch(`/api/delegate/${id}/progress`)
          const d = await r.json() as { progress: TaskProgress | null }
          if (!cancelled) setProgress(prev => ({ ...prev, [id]: d.progress }))
        } catch { /* silent */ }
      }))
    }
    void pull()
    const h = window.setInterval(pull, 1000)
    return () => { cancelled = true; clearInterval(h) }
  }, [inflight.map(t => t.id).join(',')])   // eslint-disable-line react-hooks/exhaustive-deps

  async function cancelTask(id: string) {
    try {
      await fetch(`/api/delegate/${id}/cancel`, { method: 'POST' })
      await refreshTasks()
    } catch { /* silent */ }
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Sub-tab bar: Live · History */}
      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-[var(--border)]">
        <SubTab
          icon={<Activity className="w-3 h-3" />}
          label="Live"
          badge={inflight.length}
          active={tab === 'live'}
          onClick={() => setTab('live')}
        />
        <SubTab
          icon={<HistoryIcon className="w-3 h-3" />}
          label="History"
          active={tab === 'history'}
          onClick={() => setTab('history')}
        />
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {tab === 'live' && (
          <>
            {loading && (
              <div className="text-center py-4 text-[var(--text-muted)] text-xs">
                <Loader2 className="w-3.5 h-3.5 animate-spin inline mr-1.5" />
                loading…
              </div>
            )}

            {!loading && tasks.length === 0 && activeConvId && (
              <div className="text-center py-6 space-y-2">
                <Bot className="w-8 h-8 text-[var(--text-muted)]/50 mx-auto" />
                <div className="text-[11.5px] text-[var(--text-muted)] italic px-3 leading-relaxed">
                  No auxiliary workers running.
                  <br />
                  Use the amber <span className="inline-flex items-center rounded bg-amber-400/20 text-amber-300 px-1"><Bot className="w-2.5 h-2.5" /></span> button
                  next to Send to hand a task to a specialist worker model. The
                  judge picks the best fit and it runs here in the background.
                </div>
              </div>
            )}

            {/* In-flight cards */}
            <AnimatePresence>
              {inflight.map(t => (
                <motion.div
                  key={t.id}
                  initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                >
                  <InflightCard task={t} progress={progress[t.id] ?? null} onCancel={cancelTask} />
                </motion.div>
              ))}
            </AnimatePresence>

            {/* Recent (done) — same conversation only */}
            {recentDone.length > 0 && (
              <div className="pt-2 border-t border-[var(--border)] space-y-2">
                <div className="text-[9.5px] font-mono uppercase tracking-[0.25em] text-[var(--text-muted)] px-1">
                  Recent · this conversation
                </div>
                {recentDone.map(t => <RecentCard key={t.id} task={t} />)}
              </div>
            )}
          </>
        )}

        {tab === 'history' && (
          <>
            <div className="text-[9.5px] font-mono uppercase tracking-[0.25em] text-[var(--text-muted)] px-1 mb-2">
              All conversations · newest first · {historyTasks.length}
            </div>
            {historyTasks.length === 0 ? (
              <div className="text-center py-6 space-y-2">
                <HistoryIcon className="w-8 h-8 text-[var(--text-muted)]/50 mx-auto" />
                <div className="text-[11.5px] text-[var(--text-muted)] italic px-3 leading-relaxed">
                  No completed auxiliary tasks yet.
                </div>
              </div>
            ) : (
              <div className="space-y-1.5">
                {historyTasks.map(t => <HistoryCard key={t.id} task={t} />)}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}


function SubTab({
  icon, label, badge, active, onClick,
}: {
  icon:   React.ReactNode
  label:  string
  badge?: number
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-medium transition-colors ${
        active
          ? 'text-[var(--accent)] bg-[var(--accent-dim)]/30'
          : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)]'
      }`}
    >
      {icon}
      {label}
      {badge != null && badge > 0 && (
        <span className="text-[9px] font-mono px-1 py-0.5 rounded bg-amber-400/20 text-amber-300 min-w-[14px] text-center">
          {badge}
        </span>
      )}
    </button>
  )
}


function InflightCard({
  task, progress, onCancel,
}: {
  task:     DelegatedTask
  progress: TaskProgress | null
  onCancel: (id: string) => void
}) {
  const elapsed = task.started_at
    ? Math.max(0, Math.round(Date.now() / 1000 - task.started_at))
    : 0
  const stageLabel = progress?.stage ?? (task.status === 'pending' ? 'queued' : 'starting')
  // Approximate progress: a delegate model typically emits 500-2000 tokens
  // for a research reply. Cap the bar at 1500 tokens so it fills gently
  // over a couple of minutes — good enough as a "something's happening"
  // indicator without pretending to know the true remaining time.
  const tokens = progress?.tokens ?? 0
  const pct    = Math.min(95, Math.round((tokens / 1500) * 100))
  const stageColour = stageLabel === 'commenting' || stageLabel === 'done'
    ? 'text-emerald-300'
    : stageLabel === 'streaming'
      ? 'text-amber-300'
      : 'text-[var(--text-muted)]'

  return (
    <div className="rounded-xl border border-amber-400/40 bg-amber-400/5 overflow-hidden shadow-lg shadow-amber-400/5">
      {/* ── Header: model + category + cancel ────────────────────── */}
      <div className="px-3 py-2.5 border-b border-amber-400/20 space-y-1.5">
        <div className="flex items-center gap-2">
          <div className="relative flex-shrink-0">
            <Bot className="w-4 h-4 text-amber-300" />
            <span className="absolute -bottom-0.5 -right-0.5 w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-1.5">
              <span className="text-[13px] text-[var(--text-primary)] font-medium truncate">
                {task.delegate_model.split(':')[0]}
              </span>
              <span className="text-[9.5px] font-mono px-1.5 py-0.5 rounded-full uppercase tracking-wider border border-amber-400/40 text-amber-300 bg-amber-400/10 shrink-0">
                {task.category}
              </span>
            </div>
          </div>
          <button
            onClick={() => onCancel(task.id)}
            className="p-1 rounded text-[var(--text-muted)] hover:text-red-300 hover:bg-red-500/10 transition-colors shrink-0"
            title="Cancel"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Stage + progress bar */}
        <div>
          <div className="flex items-center gap-1.5 text-[9.5px] font-mono uppercase tracking-wider">
            <Loader2 className={`w-2.5 h-2.5 animate-spin ${stageColour}`} />
            <span className={stageColour}>{stageLabel}</span>
            <span className="text-[var(--text-muted)]">·</span>
            <span className="text-[var(--text-muted)]">
              {elapsed < 60 ? `${elapsed}s` : `${Math.round(elapsed / 60)}m`}
            </span>
            {tokens > 0 && <>
              <span className="text-[var(--text-muted)]">·</span>
              <span className="text-[var(--text-muted)]">{tokens} tok</span>
              {elapsed > 0 && <>
                <span className="text-[var(--text-muted)]">·</span>
                <span className="text-[var(--accent)]">{Math.round(tokens / elapsed)} tok/s</span>
              </>}
            </>}
          </div>
          <div className="mt-1 h-0.5 rounded-full bg-[var(--bg-tertiary)] overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-amber-300 to-emerald-300 transition-all duration-500"
              style={{ width: `${Math.max(3, pct)}%` }}
            />
          </div>
        </div>
      </div>

      <div className="p-3 space-y-2.5">
        {/* Original prompt (always visible so user can verify) */}
        <div className="rounded-md border border-[var(--border)] bg-[var(--bg-tertiary)]/30 px-2.5 py-1.5">
          <div className="text-[9px] font-mono uppercase tracking-[0.22em] text-[var(--text-muted)] mb-0.5">
            Prompt
          </div>
          <div className="text-[11.5px] text-[var(--text-secondary)] line-clamp-3 leading-snug">
            {task.prompt}
          </div>
        </div>

        {/* Tool events (research/general delegates use MCP tools) */}
        {progress && progress.tool_events.length > 0 && (
          <div className="rounded-md border border-[var(--border)] bg-[var(--bg-primary)]/40 px-2.5 py-1.5 space-y-1">
            <div className="text-[9px] font-mono uppercase tracking-[0.22em] text-[var(--text-muted)] flex items-center gap-1">
              <Wrench className="w-2.5 h-2.5" /> tool calls
            </div>
            {progress.tool_events.slice(-4).map((ev, i) => (
              <div key={i} className="flex items-center gap-1.5 text-[10.5px] font-mono">
                {ev.status === 'running'
                  ? <Loader2 className="w-2.5 h-2.5 animate-spin text-amber-300 shrink-0" />
                  : <CheckCircle2 className="w-2.5 h-2.5 text-emerald-300 shrink-0" />}
                <span className="text-[var(--accent)] truncate">{ev.name.replace(/__/g, '·')}</span>
                {ev.preview && (
                  <span className="text-[var(--text-muted)] truncate italic text-[9.5px]">
                    → {ev.preview.split('\n')[0].slice(0, 46)}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Thinking — always visible when present, no click-to-expand.
            Fade at the top so scroll-in feels natural. */}
        {progress?.thinking && (
          <div className="rounded-md border border-purple-500/25 bg-purple-500/5">
            <div className="px-2.5 py-1 border-b border-purple-500/15 text-[9px] font-mono uppercase tracking-[0.22em] text-purple-300 flex items-center justify-between">
              <span className="flex items-center gap-1">
                <Brain className="w-2.5 h-2.5" /> thinking
              </span>
              <span className="text-purple-300/60">
                {Math.round(progress.thinking.length / 4)} tok
              </span>
            </div>
            <div className="relative">
              <div
                ref={el => { if (el) el.scrollTop = el.scrollHeight }}
                className="px-2.5 py-2 max-h-40 overflow-y-auto text-[10.5px] text-[var(--text-secondary)] whitespace-pre-wrap font-mono leading-relaxed"
              >
                {progress.thinking.slice(-4000)}
              </div>
              <div
                className="pointer-events-none absolute top-0 left-0 right-0 h-4"
                style={{ background: 'linear-gradient(180deg, rgba(15,15,20,0.7), transparent)' }}
              />
            </div>
          </div>
        )}

        {/* Streaming content — the actual reply */}
        <div className="rounded-md border border-[var(--accent)]/30 bg-[var(--bg-primary)]/60">
          <div className="px-2.5 py-1 border-b border-[var(--accent)]/15 text-[9px] font-mono uppercase tracking-[0.22em] text-[var(--accent)] flex items-center gap-1">
            <Zap className="w-2.5 h-2.5" /> reply
            {tokens > 0 && <span className="text-[var(--text-muted)] ml-auto">{tokens} tok</span>}
          </div>
          {progress?.content ? (
            <div
              ref={el => { if (el) el.scrollTop = el.scrollHeight }}
              className="px-2.5 py-2 max-h-56 overflow-y-auto text-[12px] text-[var(--text-primary)] whitespace-pre-wrap leading-relaxed"
            >
              {progress.content.slice(-4000)}
              <span className="inline-block w-1.5 h-3.5 bg-[var(--accent)] ml-0.5 align-middle animate-pulse" />
            </div>
          ) : (
            <div className="px-2.5 py-3 flex items-center gap-1.5 text-[10.5px] text-[var(--text-muted)] italic">
              <Loader2 className="w-3 h-3 animate-spin" />
              waiting for first token…
            </div>
          )}
        </div>
      </div>
    </div>
  )
}


function RecentCard({ task }: { task: DelegatedTask }) {
  const done = task.status === 'done'
  const failed = task.status === 'failed'
  const duration = task.completed_at && task.started_at
    ? Math.round(task.completed_at - task.started_at)
    : 0
  return (
    <div className={`rounded-lg border p-2 ${
      done ? 'border-emerald-500/25 bg-emerald-500/5'
      : failed ? 'border-red-500/25 bg-red-500/5'
      : 'border-[var(--border)] bg-[var(--bg-primary)]/40'
    }`}>
      <div className="flex items-center gap-1.5 text-[10px] font-mono">
        {done ? <CheckCircle2 className="w-3 h-3 text-emerald-300 shrink-0" />
         : failed ? <AlertTriangle className="w-3 h-3 text-red-300 shrink-0" />
         : <X className="w-3 h-3 text-[var(--text-muted)] shrink-0" />}
        <span className="text-[var(--accent)] truncate">{task.delegate_model.split(':')[0]}</span>
        <span className="text-[var(--text-muted)]">·</span>
        <span className="text-[var(--text-muted)] uppercase tracking-wider">{task.category}</span>
        {duration > 0 && <span className="text-[var(--text-muted)] ml-auto">
          {duration < 60 ? `${duration}s` : `${Math.round(duration / 60)}m`}
        </span>}
      </div>
      <div className="text-[11px] text-[var(--text-primary)] truncate mt-0.5">
        {task.prompt}
      </div>
      {failed && task.error && (
        <div className="text-[10px] text-red-300 mt-0.5 truncate">{task.error}</div>
      )}
    </div>
  )
}


// History card — like RecentCard but expandable to show the full delegate
// reply. Same shape whether the task is from this conversation or not.
function HistoryCard({ task }: { task: DelegatedTask }) {
  const [open, setOpen] = useState(false)
  const done = task.status === 'done'
  const failed = task.status === 'failed'
  const duration = task.completed_at && task.started_at
    ? Math.round(task.completed_at - task.started_at)
    : 0
  const when = task.completed_at
    ? new Date(task.completed_at * 1000)
    : task.started_at
      ? new Date(task.started_at * 1000)
      : null
  const whenStr = when
    ? when.toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      })
    : ''
  return (
    <div className={`rounded-lg border overflow-hidden ${
      done ? 'border-emerald-500/25 bg-emerald-500/5'
      : failed ? 'border-red-500/25 bg-red-500/5'
      : 'border-[var(--border)] bg-[var(--bg-primary)]/40'
    }`}>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full p-2 text-left"
      >
        <div className="flex items-center gap-1.5 text-[10px] font-mono">
          {done ? <CheckCircle2 className="w-3 h-3 text-emerald-300 shrink-0" />
           : failed ? <AlertTriangle className="w-3 h-3 text-red-300 shrink-0" />
           : <X className="w-3 h-3 text-[var(--text-muted)] shrink-0" />}
          <span className="text-[var(--accent)] truncate">{task.delegate_model.split(':')[0]}</span>
          <span className="text-[var(--text-muted)]">·</span>
          <span className="text-[var(--text-muted)] uppercase tracking-wider">{task.category}</span>
          {whenStr && <span className="text-[var(--text-muted)] ml-auto text-[9.5px]">{whenStr}</span>}
          {duration > 0 && <span className="text-[var(--text-muted)]">
            {duration < 60 ? `${duration}s` : `${Math.round(duration / 60)}m`}
          </span>}
        </div>
        <div className="text-[11.5px] text-[var(--text-primary)] mt-1 line-clamp-2">
          {task.prompt}
        </div>
        {failed && task.error && (
          <div className="text-[10px] text-red-300 mt-0.5 truncate">{task.error}</div>
        )}
      </button>
      {open && task.result && (
        <div className="px-2 pb-2 pt-1 border-t border-[var(--border)]">
          <div className="text-[9.5px] font-mono uppercase tracking-[0.2em] text-[var(--text-muted)] mb-1">
            Delegate reply
          </div>
          <div className="text-[11px] text-[var(--text-primary)] whitespace-pre-wrap max-h-64 overflow-y-auto">
            {task.result}
          </div>
        </div>
      )}
    </div>
  )
}
