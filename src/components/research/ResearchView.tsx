import { useEffect, useState, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Microscope, Search, Sparkles, Globe, FileText, Loader2, Library,
  Trash2, RefreshCw, X, ExternalLink, ChevronRight, FileDown,
} from 'lucide-react'
import { Panel } from '@/components/ui/Panel'
import { PersephoneIcon } from '@/components/PersephoneIcon'
import { clsx } from 'clsx'
import { RichMarkdown } from '@/components/markdown/RichMarkdown'
import { CoverArt } from '@/components/markdown/CoverArt'

interface RunSummary {
  id:         string
  query:      string
  status:     'pending' | 'running' | 'done' | 'failed'
  createdAt:  number
  finishedAt: number | null
  sources:    number
  chunks:     number
}

interface RunFull {
  id:         string
  query:      string
  status:     string
  reportMd:   string
  reportJson: { sources?: { n: number; url: string; title: string }[]; duration_s?: number }
  error:      string
  sources:    { id: number; url: string; title: string; ok: number; chars: number }[]
}

interface Stats { runs: number; sources: number; chunks: number; has_vec: boolean }

type Tab = 'new' | 'history' | 'kb'

export function ResearchView() {
  const [tab, setTab]         = useState<Tab>('new')
  const [stats, setStats]     = useState<Stats | null>(null)
  const [runs, setRuns]       = useState<RunSummary[]>([])
  const [openRunId, setOpen]  = useState<string | null>(null)
  const [openRun, setOpenRun] = useState<RunFull | null>(null)

  const loadStats = useCallback(async () => {
    try { setStats(await (await fetch('/api/research/stats')).json()) } catch {}
  }, [])
  const loadRuns = useCallback(async () => {
    try { setRuns((await (await fetch('/api/research/runs')).json()).runs ?? []) } catch {}
  }, [])

  useEffect(() => { loadStats(); loadRuns() }, [loadStats, loadRuns])
  useEffect(() => {
    if (!openRunId) { setOpenRun(null); return }
    fetch(`/api/research/runs/${openRunId}`).then(r => r.json()).then(setOpenRun).catch(() => {})
  }, [openRunId])

  return (
    <div className="h-full glass rounded-3xl overflow-hidden flex flex-col">
      {/* Header */}
      <div className="px-6 py-4 border-b border-[var(--border)] bg-[var(--bg-glass-strong)]">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <PersephoneIcon size={40} />
            <div>
              <h2 className="font-display text-2xl text-[var(--text-primary)] leading-none">Research</h2>
              <p className="text-xs text-[var(--text-muted)] font-mono mt-1 tracking-wider">
                {stats
                  ? `${stats.runs} runs · ${stats.sources} sources · ${stats.chunks} chunks${stats.has_vec ? '' : ' · vec disabled'}`
                  : 'loading…'}
              </p>
            </div>
          </div>
          <div className="flex bg-[var(--bg-tertiary)] rounded-xl p-1 gap-1">
            <TabBtn active={tab === 'new'}     onClick={() => setTab('new')}     icon={Sparkles} label="New" />
            <TabBtn active={tab === 'history'} onClick={() => setTab('history')} icon={Library}  label="History" />
            <TabBtn active={tab === 'kb'}      onClick={() => setTab('kb')}      icon={Search}   label="KB search" />
          </div>
        </div>
      </div>

      {/* Body */}
      <div
        className="flex-1 overflow-y-auto p-6"
        style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--scrollbar) transparent' }}
      >
        <AnimatePresence mode="wait">
          {tab === 'new' && (
            <motion.div key="new" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}>
              <ResearchRun
                onComplete={() => { loadStats(); loadRuns() }}
                onOpen={(id) => { setTab('history'); setOpen(id) }}
              />
            </motion.div>
          )}

          {tab === 'history' && (
            <motion.div key="history" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}
              className="max-w-4xl mx-auto space-y-3"
            >
              <div className="flex items-center justify-between">
                <p className="text-xs text-[var(--text-muted)] font-mono uppercase tracking-[0.22em]">
                  {runs.length} past run{runs.length === 1 ? '' : 's'}
                </p>
                <button onClick={() => { loadRuns(); loadStats() }} className="p-1.5 rounded text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors">
                  <RefreshCw className="w-3.5 h-3.5" />
                </button>
              </div>
              {runs.length === 0 && (
                <Panel className="p-10 text-center">
                  <Library className="w-10 h-10 text-[var(--text-muted)] mx-auto mb-3" />
                  <p className="font-display-italic text-[var(--text-secondary)]">No research runs yet.</p>
                </Panel>
              )}
              {runs.map(r => (
                <RunRow key={r.id} run={r} onOpen={() => setOpen(r.id)}
                  onDelete={async () => {
                    await fetch(`/api/research/runs/${r.id}`, { method: 'DELETE' })
                    if (openRunId === r.id) setOpen(null)
                    loadRuns(); loadStats()
                  }}
                />
              ))}
            </motion.div>
          )}

          {tab === 'kb' && (
            <motion.div key="kb" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }}>
              <KBSearch />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Run details overlay */}
      <AnimatePresence>
        {openRun && (
          <RunDetail run={openRun} onClose={() => setOpen(null)} />
        )}
      </AnimatePresence>
    </div>
  )
}

/* ─── New research panel (live SSE) ──────────────────────────────────────── */
function ResearchRun({ onComplete, onOpen }: {
  onComplete: () => void
  onOpen: (runId: string) => void
}) {
  const [query, setQuery]   = useState('')
  const [running, setRunning] = useState(false)
  const [events, setEvents] = useState<any[]>([])
  const [report, setReport] = useState<string>('')
  const [doneRun, setDoneRun] = useState<{ id: string; sources: any[]; duration: number } | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const scrollerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollerRef.current) scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight
  }, [events.length])

  async function start() {
    const q = query.trim()
    if (!q || running) return
    setRunning(true); setEvents([]); setReport(''); setDoneRun(null)
    abortRef.current = new AbortController()

    try {
      const res = await fetch('/api/research/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q }),
        signal: abortRef.current.signal,
      })
      const reader = res.body!.getReader()
      const dec = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += dec.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const payload = line.slice(6).trim()
          if (payload === '[DONE]') continue
          try {
            const evt = JSON.parse(payload)
            setEvents(prev => [...prev, evt])
            if (evt.phase === 'done') {
              setReport(evt.report_md ?? '')
              setDoneRun({ id: evt.run_id, sources: evt.sources ?? [], duration: evt.duration_s ?? 0 })
            }
          } catch {}
        }
      }
    } catch (err) {
      setEvents(prev => [...prev, { phase: 'failed', error: String(err) }])
    } finally {
      setRunning(false)
      onComplete()
    }
  }

  function cancel() {
    abortRef.current?.abort()
    setRunning(false)
  }

  return (
    <div className="max-w-4xl mx-auto space-y-5">
      {/* Input */}
      <Panel className="p-4 space-y-3">
        <label className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)] flex items-center gap-2">
          <Microscope className="w-3 h-3" /> Research question
        </label>
        <textarea
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) start() }}
          placeholder="e.g. What are the practical differences between sqlite-vec and lancedb for local RAG in 2026?"
          rows={3}
          disabled={running}
          className="w-full bg-[var(--bg-primary)] border border-[var(--border)] rounded-xl px-3 py-2.5 text-sm
            text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)]
            transition-colors disabled:opacity-60"
        />
        <div className="flex items-center justify-between gap-3">
          <p className="text-[10px] text-[var(--text-muted)] font-mono">
            ⌘+Enter to run · uses your reasoning model + DDG / Brave / fetch MCPs
          </p>
          {running ? (
            <button onClick={cancel} className="px-4 py-2 rounded-lg text-sm font-medium bg-red-500/15 text-red-400 border border-red-500/30 hover:bg-red-500/25 transition-colors">
              Cancel
            </button>
          ) : (
            <button
              onClick={start}
              disabled={!query.trim()}
              className="px-5 py-2 rounded-lg text-sm font-medium text-white transition-all disabled:opacity-50"
              style={{
                background: 'linear-gradient(135deg, var(--accent), var(--accent-deep))',
                boxShadow: query.trim() ? '0 8px 22px -8px var(--accent-glow)' : 'none',
              }}
            >
              Run research
            </button>
          )}
        </div>
      </Panel>

      {/* Live progress */}
      {events.length > 0 && (
        <Panel className="p-0 overflow-hidden">
          <div ref={scrollerRef} className="max-h-[260px] overflow-y-auto p-3 space-y-1 font-mono text-[11px]"
            style={{ scrollbarWidth: 'thin' }}>
            {events.map((evt, i) => <EventLine key={i} evt={evt} />)}
          </div>
        </Panel>
      )}

      {/* Final report */}
      {report && (
        <Panel className="p-5">
          {doneRun && (
            <div className="flex items-center justify-between gap-3 mb-3 pb-3 border-b border-[var(--border)]">
              <span className="text-[10px] font-mono uppercase tracking-[0.22em] text-[var(--accent)]">
                ⊹ report ready · {doneRun.duration.toFixed(1)}s · {doneRun.sources.length} sources
              </span>
              <button
                onClick={() => onOpen(doneRun.id)}
                className="text-[10px] font-mono uppercase tracking-wider text-[var(--text-muted)] hover:text-[var(--accent)] inline-flex items-center gap-1"
              >
                open full <ChevronRight className="w-3 h-3" />
              </button>
            </div>
          )}
          <ReportBody markdown={report} seed={query} />
        </Panel>
      )}
    </div>
  )
}

function EventLine({ evt }: { evt: any }) {
  const icon = ({
    started:       Sparkles,
    model:         Sparkles,
    planning:      Loader2,
    plan:          Sparkles,
    search:        Search,
    search_done:   Search,
    fetch:         Globe,
    fetch_failed:  X,
    stored:        FileText,
    synthesizing:  Loader2,
    done:          Sparkles,
    failed:        X,
  } as Record<string, any>)[evt.phase] ?? ChevronRight

  const Icon = icon
  const spinning = ['planning', 'synthesizing'].includes(evt.phase)
  const color = evt.phase === 'failed' || evt.phase === 'fetch_failed'
    ? 'text-red-400'
    : evt.phase === 'done' ? 'text-[var(--accent)]'
    : 'text-[var(--text-secondary)]'

  let text = evt.phase
  if (evt.phase === 'model')        text = `using model: ${evt.model}`
  if (evt.phase === 'plan')         text = `plan: ${evt.sub_questions.length} sub-questions`
  if (evt.phase === 'search')       text = `search: ${evt.sub_question}`
  if (evt.phase === 'search_done')  text = `  ↳ ${evt.n} hits`
  if (evt.phase === 'fetch')        text = `fetch: ${truncate(evt.title || evt.url, 80)}`
  if (evt.phase === 'fetch_failed') text = `fetch failed: ${evt.url}`
  if (evt.phase === 'stored')       text = `  ↳ stored ${evt.chunks} chunks`
  if (evt.phase === 'synthesizing') text = `synthesizing report from ${evt.sources} sources…`
  if (evt.phase === 'done')         text = `done · ${(evt.duration_s ?? 0).toFixed(1)}s · ${evt.sources?.length ?? 0} sources`
  if (evt.phase === 'failed')       text = `failed: ${evt.error}`

  return (
    <div className={clsx('flex items-start gap-2 leading-relaxed', color)}>
      <Icon className={clsx('w-3 h-3 mt-0.5 flex-shrink-0', spinning && 'animate-spin')} />
      <span className="break-all">{text}</span>
    </div>
  )
}

function truncate(s: string, n: number) { return s.length <= n ? s : s.slice(0, n - 1) + '…' }

/* ─── History rows ───────────────────────────────────────────────────────── */
function RunRow({ run, onOpen, onDelete }: { run: RunSummary; onOpen: () => void; onDelete: () => void }) {
  return (
    <Panel className="p-4 flex items-center gap-4 cursor-pointer group hover:border-[var(--border-bright)] transition-colors" onClick={onOpen}>
      <div className="w-10 h-10 rounded-xl bg-[var(--accent-dim)] flex items-center justify-center flex-shrink-0">
        <Microscope className="w-5 h-5 text-[var(--accent)]" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-[var(--text-primary)] truncate">{run.query}</div>
        <div className="text-xs text-[var(--text-muted)] mt-0.5 font-mono">
          <span className={clsx(
            run.status === 'done' && 'text-green-400',
            run.status === 'failed' && 'text-red-400',
            run.status === 'running' && 'text-amber-300',
          )}>{run.status}</span>
          {' · '}{run.sources} sources · {run.chunks} chunks · {new Date(run.createdAt).toLocaleString()}
        </div>
      </div>
      <button onClick={e => { e.stopPropagation(); onDelete() }}
        className="p-1.5 rounded text-[var(--text-muted)] hover:text-red-400 hover:bg-red-400/10 opacity-0 group-hover:opacity-100 transition-all">
        <Trash2 className="w-4 h-4" />
      </button>
    </Panel>
  )
}

/* ─── Run detail overlay ─────────────────────────────────────────────────── */
function RunDetail({ run, onClose }: { run: RunFull; onClose: () => void }) {
  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="absolute inset-0 z-20 bg-black/40 backdrop-blur-sm flex items-stretch justify-center"
      onClick={onClose}
    >
      <motion.div
        initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 20, opacity: 0 }}
        transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
        className="m-4 w-full max-w-4xl glass rounded-3xl overflow-hidden flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-5 py-4 border-b border-[var(--border)] flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="text-[10px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">Research run</div>
            <div className="text-base text-[var(--text-primary)] font-display mt-0.5">{run.query}</div>
          </div>
          {run.reportMd && (
            <a
              href={`/api/research/runs/${encodeURIComponent(run.id)}/pdf`}
              download
              title="Download this report as a PDF"
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded text-[11px] font-medium
                border border-[var(--border)] text-[var(--text-muted)]
                hover:text-[var(--accent)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)]/40
                transition-colors"
            >
              <FileDown className="w-3.5 h-3.5" />
              PDF
            </a>
          )}
          <button onClick={onClose} className="p-1.5 rounded text-[var(--text-muted)] hover:text-[var(--text-secondary)]">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4" style={{ scrollbarWidth: 'thin' }}>
          {run.status === 'failed' && (
            <Panel className="p-3 border border-red-500/40 text-sm text-red-300">
              Failed: {run.error || 'unknown error'}
            </Panel>
          )}
          {run.reportMd && <ReportBody markdown={run.reportMd} seed={run.query} />}
          {run.sources.length > 0 && (
            <div>
              <h3 className="text-xs font-mono uppercase tracking-[0.28em] text-[var(--text-muted)] mb-2">All sources</h3>
              <div className="space-y-1">
                {run.sources.map(s => (
                  <a key={s.id} href={s.url} target="_blank" rel="noreferrer"
                    className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--bg-tertiary)] border border-[var(--border)] hover:border-[var(--border-bright)] text-xs transition-colors">
                    <Globe className={clsx('w-3.5 h-3.5 flex-shrink-0', s.ok ? 'text-[var(--accent)]' : 'text-red-400')} />
                    <span className="text-[var(--text-primary)] truncate flex-1">{s.title || s.url}</span>
                    <span className="text-[10px] font-mono text-[var(--text-muted)]">{(s.chars / 1000).toFixed(1)}KB</span>
                    <ExternalLink className="w-3 h-3 text-[var(--text-muted)]" />
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>
      </motion.div>
    </motion.div>
  )
}

/* ─── KB search ──────────────────────────────────────────────────────────── */
function KBSearch() {
  const [q, setQ]           = useState('')
  const [results, setResults] = useState<any[]>([])
  const [loading, setLoading] = useState(false)

  async function search() {
    const query = q.trim()
    if (!query) return
    setLoading(true)
    try {
      const r = await fetch(`/api/research/search?q=${encodeURIComponent(query)}&k=12`)
      const d = await r.json()
      setResults(d.results ?? [])
    } finally { setLoading(false) }
  }

  return (
    <div className="max-w-4xl mx-auto space-y-4">
      <Panel className="p-3">
        <div className="flex items-center gap-2">
          <Search className="w-4 h-4 text-[var(--text-muted)] flex-shrink-0" />
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && search()}
            placeholder="Semantic search across every chunk you've researched…"
            className="flex-1 bg-transparent text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none"
          />
          <button onClick={search} disabled={!q.trim() || loading}
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-white disabled:opacity-50"
            style={{ background: 'linear-gradient(135deg, var(--accent), var(--accent-deep))' }}>
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Search'}
          </button>
        </div>
      </Panel>

      {results.length === 0 && !loading && (
        <p className="text-xs text-[var(--text-muted)] text-center py-8 font-display-italic">
          {q.trim() ? 'No matches in your knowledge base.' : 'Search returns chunks from every past research run, ranked by semantic similarity.'}
        </p>
      )}

      <div className="space-y-2">
        {results.map((r, i) => (
          <Panel key={r.chunkId} className="p-3.5 space-y-2">
            <div className="flex items-start justify-between gap-3">
              <a href={r.url} target="_blank" rel="noreferrer" className="text-xs text-[var(--accent)] hover:underline truncate flex-1 min-w-0">
                {r.title || r.url}
              </a>
              <span className="text-[9px] font-mono text-[var(--text-muted)] flex-shrink-0">
                #{i + 1} · dist {r.distance.toFixed(3)}
              </span>
            </div>
            <p className="text-xs text-[var(--text-secondary)] leading-relaxed whitespace-pre-wrap">
              {truncate(r.text, 700)}
            </p>
            <div className="text-[9px] font-mono text-[var(--text-muted)]">
              from: {truncate(r.runQuery, 120)}
            </div>
          </Panel>
        ))}
      </div>
    </div>
  )
}

/* ─── Shared bits ────────────────────────────────────────────────────────── */
function TabBtn({ active, onClick, icon: Icon, label }:
  { active: boolean; onClick: () => void; icon: React.ElementType; label: string }) {
  return (
    <button onClick={onClick}
      className={clsx('flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-200',
        active
          ? 'text-[var(--text-primary)] bg-[var(--bg-primary)] shadow-[var(--shadow-soft)]'
          : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]')}>
      <Icon className={clsx('w-3.5 h-3.5', active && 'text-[var(--accent)]')} />
      {label}
    </button>
  )
}

function ReportBody({ markdown, seed }: { markdown: string; seed?: string }) {
  return (
    <div>
      {seed && (
        <div className="mb-5">
          <CoverArt seed={seed} height={140} />
        </div>
      )}
      <RichMarkdown variant="report">{markdown}</RichMarkdown>
    </div>
  )
}
