import { useEffect, useState, useCallback } from 'react'
import { motion } from 'framer-motion'
import {
  Check, ExternalLink, Globe, Folder, Database, Brain, Code, Wrench,
  AlertCircle, RefreshCw, Loader2,
} from 'lucide-react'
import { Panel } from '@/components/ui/Panel'
import { Button } from '@/components/ui/Button'
import { clsx } from 'clsx'

interface CatalogServer {
  id: string
  name: string
  description: string
  category: string
  tags: string[]
  requires_setup: boolean
  docs_url: string
  install: {
    command: string
    args: string[]
    env_vars: Record<string, { required: boolean; description: string }>
  }
}

interface CatalogCategory {
  id: string
  name: string
  icon: string
}

interface StatusEntry {
  running: boolean
  tool_count: number
}

const ICONS: Record<string, React.ElementType> = {
  globe: Globe, folder: Folder, database: Database,
  brain: Brain, code: Code,
}

export function McpSection() {
  const [servers, setServers] = useState<CatalogServer[]>([])
  const [categories, setCategories] = useState<CatalogCategory[]>([])
  const [enabled, setEnabled] = useState<Set<string>>(new Set())
  const [status, setStatus] = useState<Record<string, StatusEntry>>({})
  const [activeCat, setActiveCat] = useState<string>('all')
  const [saving, setSaving] = useState<string | null>(null)
  const [restarting, setRestarting] = useState(false)
  const [error, setError] = useState<string>('')

  /* ── data loading ───────────────────────────────────────────────── */
  const loadCatalog = useCallback(async () => {
    const r = await fetch('/api/mcp/catalog')
    const d = await r.json()
    setServers(d.servers ?? [])
    setCategories(d.categories ?? [])
  }, [])

  const loadEnabled = useCallback(async () => {
    const r = await fetch('/api/mcp/enabled')
    const d = await r.json()
    setEnabled(new Set<string>(d.ids ?? []))
  }, [])

  const loadStatus = useCallback(async () => {
    const r = await fetch('/api/mcp/status')
    const d = await r.json()
    setStatus(d.clients ?? {})
  }, [])

  useEffect(() => {
    Promise.all([loadCatalog(), loadEnabled(), loadStatus()]).catch(e => setError(String(e)))
    const t = setInterval(loadStatus, 4000)
    return () => clearInterval(t)
  }, [loadCatalog, loadEnabled, loadStatus])

  /* ── toggle handler ─────────────────────────────────────────────── */
  async function toggle(id: string) {
    const next = new Set(enabled)
    if (next.has(id)) next.delete(id)
    else next.add(id)

    // optimistic
    setEnabled(next)
    setSaving(id)
    setError('')
    try {
      const r = await fetch('/api/mcp/enabled', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ ids: [...next] }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      // Server returns statuses — refresh from authoritative source
      await loadStatus()
    } catch (exc) {
      setError(String(exc))
      // roll back
      await loadEnabled()
    } finally {
      setSaving(null)
    }
  }

  async function restart() {
    setRestarting(true)
    setError('')
    try {
      await fetch('/api/mcp/restart', { method: 'POST' })
      await loadStatus()
    } catch (exc) {
      setError(String(exc))
    } finally {
      setRestarting(false)
    }
  }

  /* ── derived view ───────────────────────────────────────────────── */
  const visible = activeCat === 'all'
    ? servers
    : servers.filter(s => s.category === activeCat)

  const sorted = [...visible].sort((a, b) => {
    // enabled first, then no-setup, then needs-setup
    const ae = enabled.has(a.id) ? 0 : 1
    const be = enabled.has(b.id) ? 0 : 1
    if (ae !== be) return ae - be
    if (a.requires_setup !== b.requires_setup) return a.requires_setup ? 1 : -1
    return a.name.localeCompare(b.name)
  })

  const runningCount = Object.values(status).filter(s => s.running).length
  const totalTools   = Object.values(status).reduce((n, s) => n + s.tool_count, 0)

  /* ── render ─────────────────────────────────────────────────────── */
  return (
    <div className="space-y-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-display text-2xl text-[var(--text-primary)] mb-1">MCP Tools</h3>
          <p className="text-sm text-[var(--text-secondary)]">
            Toggle the tools Persephone can call. Changes spawn/stop server processes immediately.
          </p>
        </div>
        <Button variant="outline" onClick={restart} className="flex items-center gap-2 flex-shrink-0">
          <RefreshCw className={clsx('w-3.5 h-3.5', restarting && 'animate-spin')} />
          Restart
        </Button>
      </div>

      {/* Status strip */}
      <Panel className="px-4 py-3 flex items-center justify-between text-xs font-mono uppercase tracking-[0.18em] text-[var(--text-muted)]">
        <span>
          <span className="inline-block w-2 h-2 rounded-full bg-green-400 mr-2 align-middle"
            style={{ boxShadow: '0 0 8px rgba(74,222,128,0.6)' }} />
          {runningCount} running · {totalTools} tools
        </span>
        <span>{enabled.size} enabled · {servers.length} in catalog</span>
      </Panel>

      {error && (
        <Panel className="px-4 py-3 border border-red-500/40 text-sm text-red-300">
          {error}
        </Panel>
      )}

      {/* Category tabs */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <CategoryPill
          active={activeCat === 'all'}
          onClick={() => setActiveCat('all')}
          icon={Wrench}
          label="All"
        />
        {categories.map(c => (
          <CategoryPill
            key={c.id}
            active={activeCat === c.id}
            onClick={() => setActiveCat(c.id)}
            icon={ICONS[c.icon] ?? Wrench}
            label={c.name}
          />
        ))}
      </div>

      {/* Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {sorted.map(s => (
          <ServerCard
            key={s.id}
            server={s}
            enabled={enabled.has(s.id)}
            status={status[s.id]}
            saving={saving === s.id}
            onToggle={() => toggle(s.id)}
          />
        ))}
      </div>

      <p className="text-xs text-[var(--text-muted)] font-mono uppercase tracking-[0.18em]">
        Servers needing API keys (Brave, GitHub, GitLab) must be configured via env vars before launch.
      </p>
    </div>
  )
}

/* ─── category pill ─────────────────────────────────────────────────── */
function CategoryPill({
  active, onClick, icon: Icon, label,
}: {
  active: boolean
  onClick: () => void
  icon: React.ElementType
  label: string
}) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'px-3 py-1.5 rounded-full text-xs font-medium border transition-all duration-200',
        active
          ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)] shadow-[0_0_12px_var(--accent-glow)]'
          : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)] hover:text-[var(--text-secondary)]',
      )}
    >
      <Icon className="w-3 h-3 inline mr-1" />{label}
    </button>
  )
}

/* ─── single catalog card ───────────────────────────────────────────── */
function ServerCard({
  server, enabled, status, saving, onToggle,
}: {
  server:  CatalogServer
  enabled: boolean
  status?: StatusEntry
  saving:  boolean
  onToggle:() => void
}) {
  const stateLabel: string = saving
    ? 'syncing…'
    : enabled
      ? (status?.running ? `running · ${status.tool_count} tools` : 'starting…')
      : server.requires_setup ? 'needs setup' : 'ready'

  const stateClass = saving
    ? 'text-[var(--text-muted)]'
    : enabled && status?.running ? 'text-green-400'
    : enabled                    ? 'text-amber-300'
    : server.requires_setup      ? 'text-amber-400'
    : 'text-[var(--text-muted)]'

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      onClick={onToggle}
      className={clsx(
        'group relative rounded-2xl border p-4 cursor-pointer transition-all duration-300 overflow-hidden',
        enabled
          ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-[var(--shadow-glow)]'
          : 'border-[var(--border)] bg-[var(--bg-tertiary)] hover:border-[var(--border-bright)]',
      )}
    >
      {/* shimmer rim on enabled */}
      {enabled && (
        <span
          aria-hidden
          className="pointer-events-none absolute inset-0 rounded-2xl"
          style={{
            background: 'linear-gradient(135deg, transparent 30%, var(--holo-dim) 50%, transparent 70%)',
            mixBlendMode: 'overlay',
            opacity: 0.6,
          }}
        />
      )}

      <div className="relative flex items-start justify-between gap-3 mb-2">
        <div>
          <div className="text-sm font-semibold text-[var(--text-primary)] tracking-tight">
            {server.name}
          </div>
          <div className={clsx('text-[10px] font-mono uppercase tracking-[0.22em] mt-1', stateClass)}>
            {saving && <Loader2 className="inline w-3 h-3 mr-1 animate-spin" />}
            {stateLabel}
          </div>
        </div>
        <ToggleDot enabled={enabled} />
      </div>

      <p className="relative text-xs text-[var(--text-secondary)] leading-relaxed mb-3 min-h-[2.4em]">
        {server.description}
      </p>

      <div className="relative flex flex-wrap gap-1 mb-3">
        {server.tags.slice(0, 4).map(t => (
          <span
            key={t}
            className="text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded
              bg-[var(--bg-secondary)] text-[var(--text-muted)] border border-[var(--border)]"
          >
            {t}
          </span>
        ))}
      </div>

      <div className="relative flex items-center justify-between text-[11px]">
        {server.requires_setup && !enabled && (
          <span className="flex items-center gap-1 text-amber-400">
            <AlertCircle className="w-3 h-3" />API key required
          </span>
        )}
        {!server.requires_setup && !enabled && <span />}
        {enabled && status?.running && <span />}
        <a
          href={server.docs_url}
          target="_blank"
          rel="noreferrer"
          onClick={e => e.stopPropagation()}
          className="ml-auto flex items-center gap-0.5 text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
        >
          docs <ExternalLink className="w-2.5 h-2.5" />
        </a>
      </div>
    </motion.div>
  )
}

/* ─── toggle dot (visual only — whole card is the click target) ────── */
function ToggleDot({ enabled }: { enabled: boolean }) {
  return (
    <div className={clsx(
      'flex-shrink-0 w-5 h-5 rounded-full flex items-center justify-center transition-all',
      enabled ? 'bg-[var(--accent)]' : 'border border-[var(--border-bright)]',
    )}
      style={enabled ? { boxShadow: '0 0 14px var(--accent-glow)' } : undefined}
    >
      {enabled && <Check className="w-3 h-3 text-white" />}
    </div>
  )
}
