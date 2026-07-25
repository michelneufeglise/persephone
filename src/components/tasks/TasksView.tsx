import { useCallback, useEffect, useMemo, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  CalendarClock, Plus, Trash2, Play, Loader2, CheckCircle2, XCircle,
  Clock, RefreshCw, ChevronRight, Wrench, Wand2,
} from 'lucide-react'
import { clsx } from 'clsx'
import { Toggle } from '@/components/ui/Toggle'
import { useAppStore } from '@/store/appStore'
import {
  listTasks, getTask, createTask, patchTask, deleteTask, runTaskNow,
  getAvailable, fetchServerConversation,
  type PlannedTask, type PlannedTaskCreate, type PlannedTaskRun,
  type PlannerAvailable, type ScheduleKind,
} from '@/lib/planner'
import type { Conversation, Message } from '@/types'

/**
 * Hydrate a server-side conversation into the local Zustand store, or no-op
 * if it's already there. Planner-created convs live on the backend only
 * until the frontend explicitly fetches them — without this the sidebar
 * "Conversations" list and the tab strip both silently ignore them.
 *
 * Concurrent polls could race past the "already exists" check and double-add,
 * so we track in-flight ids in a module-scope Set and double-check right
 * before insert.
 */
const _hydrating = new Set<string>()

async function hydrateServerConv(convId: string): Promise<boolean> {
  if (!convId) return false
  if (useAppStore.getState().conversations.some(c => c.id === convId)) return true
  if (_hydrating.has(convId)) return true // another call is already fetching this one
  _hydrating.add(convId)

  try {
    const raw = await fetchServerConversation(convId)
    if (!raw) return false

    // Race check: another concurrent call may have added it while we were
    // awaiting the network round-trip. addConversation would silently dupe.
    if (useAppStore.getState().conversations.some(c => c.id === convId)) return true

    const messages: Message[] = raw.messages
      .filter((m): m is (typeof m) & { role: 'user' | 'assistant' | 'system' } =>
        m.role !== 'tool',
      )
      .map(m => ({
        id:              m.id,
        role:            m.role,
        content:         m.content,
        thinkingContent: m.thinkingContent ?? '',
        model:           m.model,
        timestamp:       m.timestamp,
        meta:            m.meta as any,
      }))
    const conv: Conversation = {
      id:        raw.id,
      title:     raw.title,
      model:     raw.model,
      messages,
      createdAt: raw.createdAt,
      updatedAt: raw.updatedAt,
    }
    useAppStore.getState().addConversation(conv)
    return true
  } finally {
    _hydrating.delete(convId)
  }
}

type ViewMode = { kind: 'empty' } | { kind: 'detail'; taskId: string } | { kind: 'editor'; taskId: string | null }

export function TasksView() {
  const [tasks, setTasks]         = useState<PlannedTask[]>([])
  const [loading, setLoading]     = useState(true)
  const [mode, setMode]           = useState<ViewMode>({ kind: 'empty' })
  const [available, setAvailable] = useState<PlannerAvailable>({ mcp_tools: [], skills: [], models: [] })

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const [ts, av] = await Promise.all([listTasks(), getAvailable()])
      setTasks(ts)
      setAvailable(av)
      // Hydrate any planner-created conversations we don't yet have in
      // the local store — so auto-fired runs show up in the sidebar list
      // and can be re-opened as chat tabs later.
      const known = new Set(useAppStore.getState().conversations.map(c => c.id))
      const missing = Array.from(new Set(
        ts.map(t => t.lastConvId).filter((id): id is string => !!id && !known.has(id)),
      ))
      if (missing.length) {
        await Promise.all(missing.map(id => hydrateServerConv(id)))
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])
  // Poll every 5 s so nextRun countdowns feel alive & auto-fires update the list.
  useEffect(() => {
    const h = window.setInterval(() => { void refresh() }, 5000)
    return () => window.clearInterval(h)
  }, [refresh])

  async function handleSave(create: PlannedTaskCreate, editingId: string | null) {
    if (editingId) {
      await patchTask(editingId, create)
    } else {
      const t = await createTask(create)
      setMode({ kind: 'detail', taskId: t.id })
    }
    await refresh()
  }

  async function handleDelete(id: string) {
    await deleteTask(id)
    if (mode.kind !== 'empty' && ('taskId' in mode) && mode.taskId === id) {
      setMode({ kind: 'empty' })
    }
    await refresh()
  }

  async function handleToggle(id: string, enabled: boolean) {
    await patchTask(id, { enabled })
    await refresh()
  }

  return (
    <div className="h-full glass rounded-3xl overflow-hidden flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border)] bg-[var(--bg-glass-strong)]">
        <div className="flex items-center gap-2.5">
          <CalendarClock className="w-5 h-5 text-[var(--accent)]" />
          <div>
            <div className="text-base font-medium text-[var(--text-primary)] tracking-tight">Tasks</div>
            <div className="text-xs text-[var(--text-muted)]">Scheduled prompts — each run creates a chat</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <button onClick={() => void refresh()}
            className="text-xs text-[var(--text-muted)] hover:text-[var(--accent)] p-1.5 rounded-md hover:bg-[var(--bg-tertiary)] transition-colors"
            title="Refresh">
            <RefreshCw className={clsx('w-3.5 h-3.5', loading && 'animate-spin')} />
          </button>
          <button onClick={() => setMode({ kind: 'editor', taskId: null })}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent)] text-white text-xs font-medium hover:bg-[var(--accent-hover)] transition-colors">
            <Plus className="w-3.5 h-3.5" /> New task
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: task list */}
        <div className="w-80 flex-shrink-0 border-r border-[var(--border)] overflow-y-auto bg-[var(--bg-secondary)]/40"
          style={{ scrollbarWidth: 'thin' }}>
          {loading && tasks.length === 0 ? (
            <div className="flex items-center gap-2 text-sm text-[var(--text-muted)] justify-center py-10">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading tasks…
            </div>
          ) : tasks.length === 0 ? (
            <div className="text-center py-16 px-6">
              <CalendarClock className="w-10 h-10 text-[var(--text-muted)]/40 mx-auto mb-3" />
              <div className="text-sm text-[var(--text-secondary)]">No tasks yet</div>
              <div className="text-xs text-[var(--text-muted)] mt-1">Click <b>New task</b> to schedule your first prompt.</div>
            </div>
          ) : (
            <div className="p-3 space-y-2">
              {tasks.map(t => (
                <TaskCard
                  key={t.id}
                  task={t}
                  selected={mode.kind !== 'empty' && 'taskId' in mode && mode.taskId === t.id}
                  onSelect={() => setMode({ kind: 'detail', taskId: t.id })}
                  onDelete={() => handleDelete(t.id)}
                  onToggle={next => handleToggle(t.id, next)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Right: detail or editor */}
        <div className="flex-1 overflow-y-auto bg-[var(--bg-secondary)]/20" style={{ scrollbarWidth: 'thin' }}>
          <AnimatePresence mode="wait">
            {mode.kind === 'empty' ? (
              <motion.div key="empty" className="h-full flex items-center justify-center p-10 text-center"
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
                <div className="max-w-sm text-[var(--text-muted)]">
                  <CalendarClock className="w-12 h-12 mx-auto mb-4 opacity-40" />
                  <p className="text-sm leading-relaxed">
                    Pick a task on the left to view its runs, or click <b>New task</b> to schedule one.
                  </p>
                </div>
              </motion.div>
            ) : mode.kind === 'editor' ? (
              <motion.div key={`editor-${mode.taskId ?? 'new'}`} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                <TaskEditor
                  existing={mode.taskId ? tasks.find(t => t.id === mode.taskId) ?? null : null}
                  available={available}
                  onCancel={() => setMode(mode.taskId ? { kind: 'detail', taskId: mode.taskId } : { kind: 'empty' })}
                  onSave={c => handleSave(c, mode.taskId)}
                />
              </motion.div>
            ) : (
              <motion.div key={`detail-${mode.taskId}`} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                <TaskDetail
                  taskId={mode.taskId}
                  onEdit={() => setMode({ kind: 'editor', taskId: mode.taskId })}
                  onDelete={() => handleDelete(mode.taskId)}
                  refreshList={refresh}
                />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// Task card (left list)
// ──────────────────────────────────────────────────────────────────────────
function TaskCard({
  task, selected, onSelect, onDelete, onToggle,
}: {
  task: PlannedTask
  selected: boolean
  onSelect: () => void
  onDelete: () => void
  onToggle: (next: boolean) => void
}) {
  const StatusIcon =
    task.lastStatus === 'succeeded' ? CheckCircle2 :
    task.lastStatus === 'failed'    ? XCircle :
    task.lastStatus === 'running'   ? Loader2 : Clock

  const statusColor =
    task.lastStatus === 'succeeded' ? 'text-emerald-400' :
    task.lastStatus === 'failed'    ? 'text-red-400' :
    task.lastStatus === 'running'   ? 'text-[var(--accent)]' : 'text-[var(--text-muted)]'

  return (
    <div
      onClick={onSelect}
      className={clsx(
        'group rounded-xl border p-3 cursor-pointer transition-colors',
        selected
          ? 'border-[var(--accent)] bg-[var(--accent-dim)]'
          : 'border-[var(--border)] bg-[var(--bg-tertiary)]/50 hover:border-[var(--border-bright)] hover:bg-[var(--bg-tertiary)]',
      )}
    >
      <div className="flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-[var(--text-primary)] truncate" title={task.name}>
            {task.name}
          </div>
          <div className="text-xs text-[var(--text-muted)] mt-0.5 truncate" title={task.scheduleDescription}>
            {task.scheduleDescription}
          </div>
        </div>
        <Toggle checked={task.enabled} onChange={onToggle} />
      </div>

      <div className="mt-2.5 flex items-center gap-2 text-[10px]">
        <span className={clsx('flex items-center gap-1', statusColor)}>
          <StatusIcon className={clsx('w-3 h-3', task.lastStatus === 'running' && 'animate-spin')} />
          {task.lastStatus || 'idle'}
        </span>
        <span className="text-[var(--text-muted)]">·</span>
        <span className="text-[var(--text-muted)] font-mono">
          {task.enabled && task.nextRunAt ? `next ${formatRelative(task.nextRunAt)}` : 'off'}
        </span>
        <div className="flex-1" />
        <button
          onClick={e => { e.stopPropagation(); onDelete() }}
          className="opacity-0 group-hover:opacity-100 p-1 text-[var(--text-muted)] hover:text-red-400 rounded"
          title="Delete task"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      </div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// Task detail
// ──────────────────────────────────────────────────────────────────────────
function TaskDetail({
  taskId, onEdit, onDelete, refreshList,
}: {
  taskId: string
  onEdit: () => void
  onDelete: () => void
  refreshList: () => Promise<void>
}) {
  const [data, setData] = useState<(PlannedTask & { runs: PlannedTaskRun[] }) | null>(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [runErr, setRunErr] = useState<string | null>(null)
  const { openTab, setCurrentView } = useAppStore()

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const d = await getTask(taskId)
      setData(d)
    } finally {
      setLoading(false)
    }
  }, [taskId])

  useEffect(() => { void reload() }, [reload])
  useEffect(() => {
    const h = window.setInterval(() => { void reload() }, 5000)
    return () => window.clearInterval(h)
  }, [reload])

  async function fireNow() {
    setRunning(true); setRunErr(null)
    try {
      const r = await runTaskNow(taskId)
      if (!r.ok && r.error) setRunErr(r.error)
      await reload()
      await refreshList()
      if (r.ok && r.conv_id) {
        // Hydrate the server-side conv into the local store BEFORE opening
        // the tab — openTab silently drops unknown convIds otherwise.
        await hydrateServerConv(r.conv_id)
        openTab(r.conv_id)
        setCurrentView('chat')
      }
    } catch (e: any) {
      setRunErr(String(e?.message || e))
    } finally {
      setRunning(false)
    }
  }

  if (loading || !data) {
    return (
      <div className="p-8 flex items-center gap-2 text-sm text-[var(--text-muted)]">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading…
      </div>
    )
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="font-serif text-xl text-[var(--text-primary)] truncate" title={data.name}>{data.name}</h3>
          <p className="text-xs text-[var(--text-muted)] mt-0.5">{data.scheduleDescription}</p>
        </div>
        <div className="flex-shrink-0 flex items-center gap-2">
          <button
            onClick={fireNow}
            disabled={running}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent)] text-white text-xs font-medium hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-colors"
          >
            {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
            {running ? 'Running…' : 'Run now'}
          </button>
          <button onClick={onEdit}
            className="px-3 py-1.5 rounded-lg text-xs text-[var(--text-secondary)] border border-[var(--border)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors">
            Edit
          </button>
          <button onClick={onDelete}
            className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-red-400 hover:bg-red-500/10 transition-colors" title="Delete">
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {runErr && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/30 px-3 py-2 text-xs text-red-300">
          {runErr}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <InfoTile label="Model"   value={data.model || <span className="italic">unset</span>} />
        <InfoTile label="Enabled" value={data.enabled ? 'yes' : 'no'} />
        <InfoTile label="Next run" value={data.enabled && data.nextRunAt ? formatFull(data.nextRunAt) : '—'} />
        <InfoTile label="Last run"  value={data.lastRunTs ? formatFull(data.lastRunTs * 1000) : '—'} />
      </div>

      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-4">
        <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-2 font-medium">Prompt</div>
        <pre className="text-sm text-[var(--text-primary)] whitespace-pre-wrap font-sans leading-relaxed max-h-64 overflow-y-auto">
          {data.prompt}
        </pre>
      </div>

      {(data.toolIds.length > 0 || data.skillNames.length > 0) && (
        <div className="grid grid-cols-2 gap-3">
          {data.toolIds.length > 0 && (
            <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-4">
              <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-2 font-medium flex items-center gap-1.5">
                <Wrench className="w-3 h-3" /> Allowed tools
              </div>
              <div className="flex flex-wrap gap-1">
                {data.toolIds.map(id => (
                  <span key={id} className="text-[11px] px-1.5 py-0.5 rounded bg-[var(--bg-secondary)] border border-[var(--border)] font-mono text-[var(--text-secondary)]">{id}</span>
                ))}
              </div>
            </div>
          )}
          {data.skillNames.length > 0 && (
            <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-4">
              <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-2 font-medium flex items-center gap-1.5">
                <Wand2 className="w-3 h-3" /> Skills
              </div>
              <div className="flex flex-wrap gap-1">
                {data.skillNames.map(n => (
                  <span key={n} className="text-[11px] px-1.5 py-0.5 rounded bg-[var(--accent-dim)] text-[var(--accent)]">{n}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      <div>
        <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-2 font-medium">Recent runs</div>
        {data.runs.length === 0 ? (
          <div className="text-sm text-[var(--text-muted)] italic py-4">No runs yet.</div>
        ) : (
          <div className="space-y-2">
            {data.runs.map(run => (
              <RunRow
                key={run.id}
                run={run}
                onOpenConv={async () => {
                  if (!run.convId) return
                  await hydrateServerConv(run.convId)
                  openTab(run.convId)
                  setCurrentView('chat')
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function InfoTile({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] px-4 py-3">
      <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1 font-medium">{label}</div>
      <div className="text-sm text-[var(--text-primary)]">{value}</div>
    </div>
  )
}

function RunRow({ run, onOpenConv }: { run: PlannedTaskRun; onOpenConv: () => void }) {
  const Icon = run.status === 'succeeded' ? CheckCircle2
             : run.status === 'failed'    ? XCircle
             : Loader2
  const color = run.status === 'succeeded' ? 'text-emerald-400'
              : run.status === 'failed'    ? 'text-red-400'
              : 'text-[var(--accent)]'
  return (
    <button
      onClick={onOpenConv}
      disabled={!run.convId}
      className={clsx(
        'w-full text-left rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)]/50 p-3 flex items-start gap-3 transition-colors',
        run.convId ? 'hover:border-[var(--accent)] hover:bg-[var(--bg-tertiary)] cursor-pointer' : 'opacity-60 cursor-default',
      )}
    >
      <Icon className={clsx('w-4 h-4 flex-shrink-0 mt-0.5', color, run.status === 'running' && 'animate-spin')} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 text-[11px]">
          <span className="text-[var(--text-primary)] font-medium capitalize">{run.status}</span>
          <span className="text-[var(--text-muted)]">·</span>
          <span className="text-[var(--text-muted)] font-mono">{formatFull(run.startedAt)}</span>
        </div>
        {run.outputPreview && (
          <div className="text-xs text-[var(--text-secondary)] mt-1 line-clamp-2 leading-relaxed">
            {run.outputPreview}
          </div>
        )}
        {run.error && (
          <div className="text-xs text-red-300 mt-1 line-clamp-2">{run.error}</div>
        )}
      </div>
      {run.convId && <ChevronRight className="w-4 h-4 text-[var(--text-muted)] flex-shrink-0 mt-1" />}
    </button>
  )
}

// ──────────────────────────────────────────────────────────────────────────
// Task editor
// ──────────────────────────────────────────────────────────────────────────
const SCHEDULE_TABS: { id: ScheduleKind; label: string; hint: string }[] = [
  { id: 'once',        label: 'Once',       hint: 'Fires one time at a specific moment' },
  { id: 'daily',       label: 'Daily',      hint: 'Every day at a set time' },
  { id: 'weekly',      label: 'Weekly',     hint: 'Once a week on a chosen day + time' },
  { id: 'every_n_min', label: 'Interval',   hint: 'Every N minutes' },
  { id: 'cron',        label: 'Cron',       hint: '5-field cron expression' },
]

const WEEKDAYS = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']

function TaskEditor({
  existing, available, onSave, onCancel,
}: {
  existing: PlannedTask | null
  available: PlannerAvailable
  onSave: (c: PlannedTaskCreate) => Promise<void>
  onCancel: () => void
}) {
  const [name,        setName]        = useState(existing?.name ?? '')
  const [prompt,      setPrompt]      = useState(existing?.prompt ?? '')
  const [model,       setModel]       = useState(existing?.model ?? available.models[0] ?? '')
  const [enabled,     setEnabled]     = useState(existing?.enabled ?? true)
  const [scheduleKind,  setScheduleKind]  = useState<ScheduleKind>(existing?.scheduleKind ?? 'daily')
  const [scheduleValue, setScheduleValue] = useState(existing?.scheduleValue ?? defaultValueFor('daily'))
  const [toolIds,     setToolIds]     = useState<string[]>(existing?.toolIds ?? [])
  const [skillNames,  setSkillNames]  = useState<string[]>(existing?.skillNames ?? [])
  const [saving,      setSaving]      = useState(false)
  const [error,       setError]       = useState<string | null>(null)

  const canSave = name.trim() && prompt.trim() && model && scheduleValue.trim() && !saving

  async function save() {
    setSaving(true); setError(null)
    try {
      await onSave({ name: name.trim(), prompt, model, scheduleKind, scheduleValue,
                     toolIds, skillNames, enabled })
    } catch (e: any) {
      setError(String(e?.message || e))
    } finally {
      setSaving(false)
    }
  }

  function toggleIn(list: string[], setList: (v: string[]) => void, id: string) {
    setList(list.includes(id) ? list.filter(x => x !== id) : [...list, id])
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-6">
      <div>
        <h3 className="font-serif text-xl text-[var(--text-primary)]">
          {existing ? 'Edit task' : 'New task'}
        </h3>
        <p className="text-xs text-[var(--text-muted)] mt-0.5">
          Every run creates a new chat conversation with the reply.
        </p>
      </div>

      {error && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/30 px-3 py-2 text-xs text-red-300">
          {error}
        </div>
      )}

      {/* Name */}
      <Field label="Name">
        <input value={name} onChange={e => setName(e.target.value)}
          placeholder="Morning briefing"
          className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)]" />
      </Field>

      {/* Prompt */}
      <Field label="Prompt" hint="What should the model do at each run?">
        <textarea value={prompt} onChange={e => setPrompt(e.target.value)}
          rows={5} placeholder="Check my inbox for anything urgent and summarise the top 3 items."
          className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] resize-y font-sans leading-relaxed" />
      </Field>

      {/* Model */}
      <Field label="Model">
        {available.models.length === 0 ? (
          <div className="text-xs text-[var(--text-muted)] italic">No local models installed.</div>
        ) : (
          <select value={model} onChange={e => setModel(e.target.value)}
            className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]">
            {available.models.map(m => <option key={m} value={m}>{m}</option>)}
          </select>
        )}
      </Field>

      {/* Schedule */}
      <Field label="Schedule">
        <div className="flex gap-1 mb-3 flex-wrap">
          {SCHEDULE_TABS.map(t => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                setScheduleKind(t.id)
                setScheduleValue(defaultValueFor(t.id))
              }}
              className={clsx(
                'px-3 py-1.5 rounded-md text-xs font-medium border transition-colors',
                scheduleKind === t.id
                  ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                  : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
        <ScheduleInput kind={scheduleKind} value={scheduleValue} onChange={setScheduleValue} />
        <p className="text-[11px] text-[var(--text-muted)] mt-2">
          {SCHEDULE_TABS.find(t => t.id === scheduleKind)?.hint}
        </p>
      </Field>

      {/* MCP tools */}
      <Field label={`MCP tools · ${available.mcp_tools.length}`} hint="Only these tools will be exposed to the task. Empty = no tools.">
        {available.mcp_tools.length === 0 ? (
          <div className="text-xs text-[var(--text-muted)] italic">No MCP servers running. Enable some in Settings → Tools.</div>
        ) : (
          <div className="max-h-48 overflow-y-auto border border-[var(--border)] rounded-lg bg-[var(--bg-tertiary)] p-2 space-y-1">
            {available.mcp_tools.map(t => (
              <label key={t.id} className="flex items-start gap-2 px-2 py-1.5 rounded hover:bg-[var(--bg-secondary)] cursor-pointer text-xs">
                <input type="checkbox" checked={toolIds.includes(t.id)} onChange={() => toggleIn(toolIds, setToolIds, t.id)} className="mt-0.5" />
                <div className="min-w-0 flex-1">
                  <div className="text-[var(--text-primary)] font-mono truncate">{t.name}</div>
                  {t.description && <div className="text-[var(--text-muted)] text-[11px] truncate">{t.description}</div>}
                </div>
              </label>
            ))}
          </div>
        )}
      </Field>

      {/* Skills */}
      <Field label={`Skills · ${available.skills.length}`} hint="These skill bodies get injected verbatim (no judge pass).">
        {available.skills.length === 0 ? (
          <div className="text-xs text-[var(--text-muted)] italic">No skills discovered.</div>
        ) : (
          <div className="max-h-48 overflow-y-auto border border-[var(--border)] rounded-lg bg-[var(--bg-tertiary)] p-2 space-y-1">
            {available.skills.map(sk => (
              <label key={sk.name} className="flex items-start gap-2 px-2 py-1.5 rounded hover:bg-[var(--bg-secondary)] cursor-pointer text-xs">
                <input type="checkbox" checked={skillNames.includes(sk.name)} onChange={() => toggleIn(skillNames, setSkillNames, sk.name)} className="mt-0.5" />
                <div className="min-w-0 flex-1">
                  <div className="text-[var(--text-primary)] font-mono truncate">{sk.name}</div>
                  {sk.description && <div className="text-[var(--text-muted)] text-[11px] truncate">{sk.description}</div>}
                </div>
              </label>
            ))}
          </div>
        )}
      </Field>

      {/* Enabled */}
      <div className="flex items-center gap-3">
        <Toggle checked={enabled} onChange={setEnabled} label="Enabled" description="Only enabled tasks fire on schedule." />
      </div>

      {/* Actions */}
      <div className="flex items-center justify-end gap-2 pt-2 border-t border-[var(--border)]">
        <button onClick={onCancel}
          className="px-4 py-2 rounded-lg text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)] transition-colors">
          Cancel
        </button>
        <button onClick={save} disabled={!canSave}
          className="px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] transition-colors disabled:opacity-50 flex items-center gap-2">
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {existing ? 'Save changes' : 'Create task'}
        </button>
      </div>
    </div>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wider text-[var(--text-muted)] mb-1.5 font-medium">{label}</div>
      {children}
      {hint && <p className="text-[11px] text-[var(--text-muted)] mt-1.5">{hint}</p>}
    </div>
  )
}

function ScheduleInput({ kind, value, onChange }: { kind: ScheduleKind; value: string; onChange: (v: string) => void }) {
  if (kind === 'once') {
    return (
      <input type="datetime-local" value={value} onChange={e => onChange(e.target.value)}
        className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]" />
    )
  }
  if (kind === 'daily') {
    return (
      <input type="time" value={value} onChange={e => onChange(e.target.value)}
        className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]" />
    )
  }
  if (kind === 'weekly') {
    // value shape: 'MON HH:MM'
    const parts = (value || 'MON 09:00').split(/\s+/)
    const day = (parts[0] || 'MON').toUpperCase()
    const time = parts[1] || '09:00'
    return (
      <div className="flex gap-2">
        <select value={day} onChange={e => onChange(`${e.target.value} ${time}`)}
          className="px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]">
          {WEEKDAYS.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        <input type="time" value={time} onChange={e => onChange(`${day} ${e.target.value}`)}
          className="flex-1 px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]" />
      </div>
    )
  }
  if (kind === 'every_n_min') {
    return (
      <div className="flex items-center gap-2">
        <span className="text-sm text-[var(--text-muted)]">every</span>
        <input type="number" min={1} value={value} onChange={e => onChange(e.target.value)}
          className="w-24 px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]" />
        <span className="text-sm text-[var(--text-muted)]">minutes</span>
      </div>
    )
  }
  // cron
  return (
    <input value={value} onChange={e => onChange(e.target.value)}
      placeholder="0 9 * * MON-FRI"
      className="w-full px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] font-mono" />
  )
}

function defaultValueFor(kind: ScheduleKind): string {
  const now = new Date()
  if (kind === 'once') {
    // 15 minutes from now in `datetime-local` format
    const dt = new Date(now.getTime() + 15 * 60_000)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${dt.getFullYear()}-${pad(dt.getMonth()+1)}-${pad(dt.getDate())}T${pad(dt.getHours())}:${pad(dt.getMinutes())}`
  }
  if (kind === 'daily')       return '09:00'
  if (kind === 'weekly')      return 'MON 09:00'
  if (kind === 'every_n_min') return '30'
  return '0 9 * * MON-FRI'
}

// ──────────────────────────────────────────────────────────────────────────
// Time formatting
// ──────────────────────────────────────────────────────────────────────────
function formatRelative(ms: number): string {
  const diff = ms - Date.now()
  const abs = Math.abs(diff)
  const s = Math.floor(abs / 1000)
  const m = Math.floor(s / 60)
  const h = Math.floor(m / 60)
  const d = Math.floor(h / 24)
  const sign = diff < 0 ? 'ago' : 'in'
  if (d > 0) return `${sign === 'in' ? 'in ' : ''}${d}d${sign === 'ago' ? ' ago' : ''}`
  if (h > 0) return `${sign === 'in' ? 'in ' : ''}${h}h${sign === 'ago' ? ' ago' : ''}`
  if (m > 0) return `${sign === 'in' ? 'in ' : ''}${m}m${sign === 'ago' ? ' ago' : ''}`
  return `${sign === 'in' ? 'in ' : ''}${s}s${sign === 'ago' ? ' ago' : ''}`
}

function formatFull(ms: number): string {
  const d = new Date(ms)
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

// Silence "declared but never used" for imports that we keep for prop typings
void useMemo
