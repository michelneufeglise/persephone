import { useEffect, useMemo, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  ChevronDown, Cpu, Wrench, Brain, Search, Eye, Sparkles,
  Layers, Box, X, ExternalLink, HardDrive, Wand2,
} from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { fetchModels } from '@/lib/ollama'
import { resolveModelMeta, isChatModel, typeLabel, type ModelMeta } from '@/lib/modelMeta'
import type { OllamaModel } from '@/types'
import { clsx } from 'clsx'

interface EnabledMcp { id: string; name: string }
interface OllamaDetails {
  parameters?:    string
  template?:      string
  modelfile?:     string
  details?: {
    family?:              string
    families?:            string[]
    format?:              string
    parameter_size?:      string
    quantization_level?:  string
  }
}

export function ModelSelector() {
  const { models, setModels, settings, updateSettings } = useAppStore()
  const activeModel = settings.activeModel
  const [mcps, setMcps] = useState<EnabledMcp[]>([])
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const popoverRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  /* ── load enabled MCPs (for the tools badge) ─────────────────── */
  useEffect(() => {
    fetch('/api/mcp/enabled').then(r => r.json()).then(d => setMcps(d.servers ?? [])).catch(() => {})
  }, [])

  /* ── load installed models + default selection ──────────────── */
  useEffect(() => {
    fetchModels()
      .then(m => {
        setModels(m)
        const installed = (id: string) => m.some(x => x.name === id)
        if (!settings.activeModel || !installed(settings.activeModel)) {
          const chat = m.filter(x => isChatModel(resolveModelMeta(x.name)))
          const preferred =
            chat.find(x => x.name === 'gemma4:12b')?.name ??
            chat.find(x => x.name.toLowerCase().startsWith('gemma4'))?.name ??
            chat[0]?.name
          if (preferred) updateSettings({ activeModel: preferred })
        }
      })
      .catch(console.error)
  }, [])

  /* ── close on outside-click / Esc ────────────────────────────── */
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (popoverRef.current?.contains(e.target as Node)) return
      if (triggerRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  /* ── chat-only filtered list, ranked & searched ─────────────── */
  const chatModels = useMemo(() => {
    return models
      .map(m => ({ raw: m, meta: resolveModelMeta(m.name) }))
      .filter(x => isChatModel(x.meta))
  }, [models])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return chatModels
    return chatModels.filter(({ raw, meta }) =>
      raw.name.toLowerCase().includes(q)
      || meta.displayName.toLowerCase().includes(q)
      || meta.tagline?.toLowerCase().includes(q)
      || meta.strengths.some(s => s.toLowerCase().includes(q))
      || meta.vendor?.toLowerCase().includes(q)
      || typeLabel(meta.type).toLowerCase().includes(q),
    )
  }, [chatModels, query])

  const activeMeta = activeModel ? resolveModelMeta(activeModel) : null
  const thinkingActive = activeMeta?.supportsThinking ?? false

  const autoRoute = settings.autoRoute

  return (
    <div className="relative flex items-center gap-1.5 flex-wrap">
      <Cpu className="w-4 h-4 text-[var(--text-muted)] flex-shrink-0" />

      {/* Trigger */}
      <button
        ref={triggerRef}
        onClick={() => setOpen(o => !o)}
        className="group inline-flex items-center gap-2 bg-[var(--bg-tertiary)] border border-[var(--border)]
          rounded-lg pl-2.5 pr-2 py-1.5 text-xs text-[var(--text-primary)] font-mono cursor-pointer
          hover:border-[var(--border-bright)] focus:outline-none focus:border-[var(--accent)] transition-all"
        title={
          autoRoute
            ? `Auto-route on · preferred: ${activeModel}`
            : (activeMeta?.tagline ?? activeModel)
        }
      >
        <span className="truncate max-w-[180px]">{activeModel || 'Loading…'}</span>
        {activeMeta && (
          <TypeChip meta={activeMeta} mini />
        )}
        <ChevronDown className={clsx('w-3.5 h-3.5 text-[var(--text-muted)] transition-transform', open && 'rotate-180')} />
      </button>

      {/* Auto-route toggle */}
      <button
        onClick={() => updateSettings({ autoRoute: !autoRoute })}
        title={
          autoRoute
            ? 'Auto-route ON — server picks the best installed model per turn'
            : 'Auto-route OFF — always uses the selected model'
        }
        className={clsx(
          'inline-flex items-center gap-1 px-2 py-1.5 rounded-lg border text-[10px] font-mono uppercase tracking-wider transition-all',
          autoRoute
            ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)] shadow-[0_0_10px_var(--accent-glow)]'
            : 'border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
        )}
      >
        <Wand2 className="w-3 h-3" />
        auto
        <span
          className={clsx(
            'ml-1 relative inline-block w-6 h-3 rounded-full transition-colors',
            autoRoute ? 'bg-[var(--accent)]' : 'bg-[var(--bg-secondary)]',
          )}
        >
          <motion.span
            layout
            transition={{ type: 'spring', stiffness: 700, damping: 30 }}
            className="absolute top-0.5 w-2 h-2 rounded-full bg-white shadow"
            style={{ left: autoRoute ? '14px' : '2px' }}
          />
        </span>
      </button>

      {/* Badges (always visible) */}
      {thinkingActive && (
        <div
          className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-[var(--accent-dim)] border border-[var(--border-bright)] text-[10px] text-[var(--accent)]"
          title="Native thinking mode is on"
        >
          <Brain className="w-3 h-3" />
          thinking
        </div>
      )}
      {mcps.length > 0 && (
        <div
          className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-[var(--bg-tertiary)] border border-[var(--border)] text-[10px] text-[var(--text-secondary)]"
          title={`Connected tools: ${mcps.map(m => m.name).join(', ')}`}
        >
          <Wrench className="w-3 h-3" />
          {mcps.length} tool{mcps.length === 1 ? '' : 's'}
        </div>
      )}

      {/* Popover */}
      <AnimatePresence>
        {open && (
          <motion.div
            ref={popoverRef}
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0,  scale: 1 }}
            exit={{    opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
            className="absolute left-0 top-full mt-2 w-[460px] z-50 rounded-2xl glass glass-strong overflow-hidden"
            style={{ boxShadow: 'var(--shadow-deep)' }}
          >
            {/* Search */}
            <div className="flex items-center gap-2 px-3 py-2.5 border-b border-[var(--border)]">
              <Search className="w-3.5 h-3.5 text-[var(--text-muted)]" />
              <input
                autoFocus
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder="Search models, vendors, strengths…"
                className="flex-1 bg-transparent text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none"
              />
              {query && (
                <button onClick={() => setQuery('')} className="text-[var(--text-muted)] hover:text-[var(--text-secondary)]">
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>

            {/* List */}
            <div
              className="max-h-[420px] overflow-y-auto p-2 space-y-1"
              style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--scrollbar) transparent' }}
            >
              {filtered.length === 0 && (
                <p className="text-xs text-[var(--text-muted)] text-center py-8 font-display-italic">
                  No models match "{query}"
                </p>
              )}
              {filtered.map(({ raw, meta }) => (
                <ModelRow
                  key={raw.name}
                  raw={raw}
                  meta={meta}
                  active={raw.name === activeModel}
                  onSelect={() => {
                    updateSettings({ activeModel: raw.name })
                    setOpen(false)
                  }}
                />
              ))}
            </div>

            {/* Footer */}
            <div className="flex items-center justify-between px-3 py-2 border-t border-[var(--border)] text-[10px] font-mono uppercase tracking-[0.22em] text-[var(--text-muted)]">
              <span>{chatModels.length} models installed</span>
              <a
                href="https://ollama.com/library"
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1 hover:text-[var(--accent)] transition-colors"
              >
                browse library <ExternalLink className="w-2.5 h-2.5" />
              </a>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/* ─── individual row ───────────────────────────────────────────────── */
function ModelRow({
  raw, meta, active, onSelect,
}: {
  raw: OllamaModel
  meta: ModelMeta
  active: boolean
  onSelect: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [details, setDetails]   = useState<OllamaDetails | null>(null)
  const [loading, setLoading]   = useState(false)

  // Lazy-load Ollama /api/show on first expansion
  async function loadDetails() {
    if (details || loading) return
    setLoading(true)
    try {
      const r = await fetch(`/api/models/details/${encodeURIComponent(raw.name)}`)
      if (r.ok) setDetails(await r.json())
    } catch { /* swallow */ }
    finally { setLoading(false) }
  }

  // Prefer Ollama's actual reported param size + ctx when available
  const paramsLabel = details?.details?.parameter_size ?? meta.paramsLabel
  const quant       = details?.details?.quantization_level
  const sizeMB      = raw.size ? Math.round(raw.size / 1e6) : null

  return (
    <motion.div
      layout
      className={clsx(
        'group relative rounded-xl border p-3 cursor-pointer transition-all duration-200 overflow-hidden',
        active
          ? 'border-[var(--accent)] bg-[var(--accent-dim)] shadow-[var(--shadow-glow)]'
          : 'border-transparent hover:border-[var(--border-bright)] hover:bg-[var(--bg-tertiary)]',
      )}
      onClick={onSelect}
    >
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className="text-[13px] font-semibold text-[var(--text-primary)] tracking-tight truncate">
              {meta.displayName}
            </span>
            <TypeChip meta={meta} />
            {paramsLabel && (
              <span className="text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded
                bg-[var(--bg-secondary)] border border-[var(--border)] text-[var(--text-secondary)]">
                {paramsLabel}
              </span>
            )}
            {meta.contextK && (
              <span className="text-[9px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded
                bg-[var(--bg-secondary)] border border-[var(--border)] text-[var(--text-muted)]">
                {meta.contextK}K ctx
              </span>
            )}
            {meta.supportsTools && (
              <span title="Tool calling" className="text-[var(--accent)]"><Wrench className="w-3 h-3" /></span>
            )}
            {meta.supportsVision && (
              <span title="Vision input" className="text-[var(--holo)]"><Eye className="w-3 h-3" /></span>
            )}
            {meta.supportsThinking && (
              <span title="Native thinking" className="text-[var(--gold)]"><Brain className="w-3 h-3" /></span>
            )}
          </div>

          <p className="text-[11px] text-[var(--text-secondary)] leading-relaxed mb-1">
            {meta.tagline}
          </p>

          <div className="text-[10px] font-mono text-[var(--text-muted)] truncate">
            {raw.name}
            {sizeMB && <span className="ml-2">· {(sizeMB / 1000).toFixed(1)}GB</span>}
            {quant   && <span className="ml-2">· {quant}</span>}
          </div>
        </div>

        <button
          onClick={(e) => { e.stopPropagation(); setExpanded(x => !x); loadDetails() }}
          className="flex-shrink-0 p-1 rounded text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
          title="Details"
        >
          <ChevronDown className={clsx('w-3.5 h-3.5 transition-transform', expanded && 'rotate-180')} />
        </button>
      </div>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{    height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
            onClick={e => e.stopPropagation()}
          >
            <div className="mt-3 pt-3 border-t border-[var(--border)] space-y-2.5">
              {/* Strengths */}
              <div>
                <div className="text-[9px] font-mono uppercase tracking-[0.22em] text-[var(--text-muted)] mb-1.5 flex items-center gap-1">
                  <Sparkles className="w-2.5 h-2.5" /> Strengths
                </div>
                <ul className="space-y-1">
                  {meta.strengths.map(s => (
                    <li key={s} className="text-[11px] text-[var(--text-secondary)] flex gap-1.5">
                      <span className="text-[var(--accent)]">·</span>
                      {s}
                    </li>
                  ))}
                </ul>
              </div>

              {meta.bestFor && (
                <div>
                  <div className="text-[9px] font-mono uppercase tracking-[0.22em] text-[var(--text-muted)] mb-1">Best for</div>
                  <p className="text-[11px] text-[var(--text-secondary)] leading-relaxed">{meta.bestFor}</p>
                </div>
              )}

              {/* Specs grid */}
              <div className="grid grid-cols-2 gap-2 text-[10px] font-mono">
                <Spec icon={Layers}   label="Type"     value={typeLabel(meta.type)} />
                <Spec icon={Box}      label="Params"   value={paramsLabel ?? '—'} />
                <Spec icon={HardDrive} label="Quant"   value={quant ?? '—'} />
                <Spec icon={Sparkles} label="Vendor"   value={meta.vendor ?? '—'} />
              </div>

              {meta.license && (
                <div className="text-[9px] text-[var(--text-muted)] font-mono">
                  License: {meta.license}
                  {meta.releasedYear && <> · Released {meta.releasedYear}</>}
                </div>
              )}

              {loading && (
                <div className="text-[10px] text-[var(--text-muted)] italic">Loading Ollama details…</div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

function Spec({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: string }) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-1 rounded bg-[var(--bg-secondary)] border border-[var(--border)]">
      <Icon className="w-3 h-3 text-[var(--text-muted)] flex-shrink-0" />
      <div className="min-w-0">
        <div className="text-[8px] uppercase tracking-wider text-[var(--text-muted)]">{label}</div>
        <div className="text-[10px] text-[var(--text-primary)] truncate">{value}</div>
      </div>
    </div>
  )
}

function TypeChip({ meta, mini = false }: { meta: ModelMeta; mini?: boolean }) {
  const color =
    meta.type === 'moe'    ? 'text-[var(--gold)] border-[var(--gold-dim)] bg-[var(--gold-dim)]'
  : meta.type === 'vision' ? 'text-[var(--holo)] border-[var(--holo-dim)] bg-[var(--holo-dim)]'
  : 'text-[var(--accent)] border-[var(--accent-dim)] bg-[var(--accent-dim)]'
  return (
    <span
      className={clsx(
        'inline-flex items-center font-mono uppercase tracking-wider rounded border',
        mini ? 'text-[8px] px-1 py-px' : 'text-[9px] px-1.5 py-0.5',
        color,
      )}
    >
      {typeLabel(meta.type)}
    </span>
  )
}
