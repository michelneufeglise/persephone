import { useEffect, useState, useRef, useCallback } from 'react'
import { motion } from 'framer-motion'
import {
  Upload, FileText, Trash2, FileScan, Languages, Sparkles,
  Table as TableIcon, Tags, MessageCircle, Eye, Shield, Download,
  ChevronLeft, RefreshCw, Loader2, Wand2, Copy, Check,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useAppStore } from '@/store/appStore'
import {
  listDocuments, uploadDocument, deleteDocument, getDocument,
  idp, exportDoc, pageImageUrl,
} from '@/lib/idp'
import type { IDPDocument } from '@/types'

type Tab = 'overview' | 'ocr' | 'summarize' | 'qa' | 'tables' | 'entities' | 'translate' | 'redact' | 'humanize' | 'export'

const TABS: { id: Tab; label: string; icon: React.ElementType; needsDoc: boolean }[] = [
  { id: 'overview',  label: 'Overview',   icon: Eye,           needsDoc: true },
  { id: 'ocr',       label: 'OCR',        icon: FileScan,      needsDoc: true },
  { id: 'summarize', label: 'Summary',    icon: Sparkles,      needsDoc: true },
  { id: 'qa',        label: 'Q & A',      icon: MessageCircle, needsDoc: true },
  { id: 'tables',    label: 'Tables',     icon: TableIcon,     needsDoc: true },
  { id: 'entities',  label: 'Entities',   icon: Tags,          needsDoc: true },
  { id: 'translate', label: 'Translate',  icon: Languages,     needsDoc: true },
  { id: 'redact',    label: 'Redact',     icon: Shield,        needsDoc: true },
  { id: 'humanize',  label: 'Humanize',   icon: Wand2,         needsDoc: true },
  { id: 'export',    label: 'Export',     icon: Download,      needsDoc: true },
]

export function DocumentsPanel() {
  const { activeDocId, setActiveDocId } = useAppStore()
  const [docs, setDocs] = useState<IDPDocument[]>([])
  const [activeDoc, setActiveDoc] = useState<IDPDocument | null>(null)
  const [tab, setTab] = useState<Tab>('overview')
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const refresh = useCallback(async () => {
    setDocs(await listDocuments())
  }, [])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (activeDocId) {
      getDocument(activeDocId).then(d => setActiveDoc(d))
    } else {
      setActiveDoc(null)
    }
  }, [activeDocId])

  async function handleUpload(file: File) {
    setUploading(true)
    try {
      const doc = await uploadDocument(file)
      if (doc) {
        await refresh()
        setActiveDocId(doc.id)
        setTab('overview')
      }
    } catch (err) {
      console.error('Upload failed', err)
    } finally {
      setUploading(false)
    }
  }

  async function handleDelete(id: string) {
    await deleteDocument(id)
    if (activeDocId === id) setActiveDocId(null)
    await refresh()
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) handleUpload(file)
  }

  // No doc selected: show upload + library
  if (!activeDoc) {
    return (
      <div className="h-full glass rounded-3xl overflow-hidden flex flex-col">
        <PanelHeader />

        {/* Upload zone */}
        <div
          onDrop={onDrop}
          onDragOver={e => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onClick={() => fileRef.current?.click()}
          className={clsx(
            'mx-6 mt-5 mb-4 p-10 rounded-2xl border-2 border-dashed cursor-pointer transition-all text-center bg-[var(--bg-secondary)]/70',
            dragOver
              ? 'border-[var(--accent)] bg-[var(--accent-dim)]'
              : 'border-[var(--border)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)]',
          )}
        >
          <input
            type="file"
            ref={fileRef}
            className="hidden"
            onChange={e => e.target.files?.[0] && handleUpload(e.target.files[0])}
            accept=".pdf,.docx,.xlsx,.csv,.txt,.md,.png,.jpg,.jpeg"
          />
          {uploading ? (
            <div className="flex flex-col items-center gap-3 text-[var(--accent)]">
              <motion.div className="w-10 h-10 rounded-full border-2 border-[var(--accent)] border-t-transparent"
                animate={{ rotate: 360 }} transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }} />
              <span className="text-sm">Processing…</span>
            </div>
          ) : (
            <>
              <Upload className="w-10 h-10 text-[var(--accent)] mx-auto mb-3" />
              <p className="text-sm text-[var(--text-primary)] font-medium">Drop a document or click to upload</p>
              <p className="text-xs text-[var(--text-muted)] mt-1.5">PDF, DOCX, XLSX, CSV, TXT, MD, PNG, JPEG</p>
            </>
          )}
        </div>

        {/* Library */}
        <div className="flex-1 overflow-y-auto px-6 pb-6 space-y-1.5"
          style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--scrollbar) transparent' }}>
          {docs.length === 0 ? (
            <div className="text-center text-sm text-[var(--text-muted)] py-10">
              No documents yet. Upload one to begin.
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between mb-2 mt-1">
                <span className="text-[11px] text-[var(--text-muted)] uppercase tracking-wider">Library ({docs.length})</span>
                <button onClick={refresh} className="text-[var(--text-muted)] hover:text-[var(--accent)] p-1.5 rounded-md hover:bg-[var(--bg-tertiary)] transition-colors">
                  <RefreshCw className="w-3.5 h-3.5" />
                </button>
              </div>
              {docs.map(d => (
                <DocLibItem key={d.id} doc={d}
                  onSelect={() => setActiveDocId(d.id)}
                  onDelete={() => handleDelete(d.id)} />
              ))}
            </>
          )}
        </div>
      </div>
    )
  }

  // Doc selected: show tabs + content
  return (
    <div className="h-full glass rounded-3xl overflow-hidden flex flex-col">
      <PanelHeader />

      {/* Document banner */}
      <div className="px-6 py-3.5 border-b border-[var(--border)] bg-[var(--bg-glass-strong)] flex items-center gap-3">
        <button
          onClick={() => setActiveDocId(null)}
          className="p-1.5 text-[var(--text-muted)] hover:text-[var(--accent)] rounded-md hover:bg-[var(--bg-tertiary)] transition-colors"
          title="Back to library"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <FileText className="w-4 h-4 text-[var(--accent)] flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-[var(--text-primary)] truncate" title={activeDoc.filename}>
            {activeDoc.filename}
          </div>
          <div className="text-xs text-[var(--text-muted)]">
            {activeDoc.pages} {activeDoc.pages === 1 ? 'page' : 'pages'} · {formatBytes(activeDoc.size)}
          </div>
        </div>
      </div>

      {/* Tab strip */}
      <div className="flex items-center px-4 py-2 border-b border-[var(--border)] gap-1 overflow-x-auto bg-[var(--bg-secondary)]/60"
        style={{ scrollbarWidth: 'none' }}>
        {TABS.map(t => {
          const Icon = t.icon
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              title={t.label}
              className={clsx(
                'flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors whitespace-nowrap',
                tab === t.id
                  ? 'bg-[var(--accent-dim)] text-[var(--accent)]'
                  : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]',
              )}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          )
        })}
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto bg-[var(--bg-secondary)]/40" style={{ scrollbarWidth: 'thin' }}>
        {tab === 'overview'  && <OverviewTab doc={activeDoc} />}
        {tab === 'ocr'       && <ActionTab doc={activeDoc} run={d => idp.ocr(d.id)}        label="Run OCR" hint="Reads images using your configured OCR model." />}
        {tab === 'summarize' && <SummarizeTab doc={activeDoc} />}
        {tab === 'qa'        && <QATab doc={activeDoc} />}
        {tab === 'tables'    && <TablesTab doc={activeDoc} />}
        {tab === 'entities'  && <EntitiesTab doc={activeDoc} />}
        {tab === 'translate' && <TranslateTab doc={activeDoc} />}
        {tab === 'redact'    && <RedactTab doc={activeDoc} />}
        {tab === 'humanize'  && <HumanizeTab doc={activeDoc} />}
        {tab === 'export'    && <ExportTab doc={activeDoc} />}
      </div>
    </div>
  )
}

// ── Header ──────────────────────────────────────────────────────────
function PanelHeader() {
  return (
    <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border)] bg-[var(--bg-glass-strong)]">
      <div className="flex items-center gap-2.5">
        <FileText className="w-4 h-4 text-[var(--accent)]" />
        <span className="text-base font-medium text-[var(--text-primary)] tracking-tight">Documents</span>
      </div>
    </div>
  )
}

// ── Library item ────────────────────────────────────────────────────
function DocLibItem({ doc, onSelect, onDelete }: { doc: IDPDocument; onSelect: () => void; onDelete: () => void }) {
  return (
    <div
      onClick={onSelect}
      className="group flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer bg-[var(--bg-tertiary)]/40 hover:bg-[var(--bg-tertiary)] border border-transparent hover:border-[var(--border)] transition-colors"
    >
      <FileText className="w-4 h-4 text-[var(--accent)] flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-[var(--text-primary)] truncate" title={doc.filename}>
          {doc.filename}
        </div>
        <div className="text-xs text-[var(--text-muted)] mt-0.5">
          {doc.pages} page{doc.pages === 1 ? '' : 's'} · {formatBytes(doc.size)}
        </div>
      </div>
      <button
        onClick={e => { e.stopPropagation(); onDelete() }}
        className="opacity-0 group-hover:opacity-100 p-1.5 text-[var(--text-muted)] hover:text-red-400 rounded-md hover:bg-red-500/10 transition-all"
      >
        <Trash2 className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}

// ── Overview tab ────────────────────────────────────────────────────
function OverviewTab({ doc }: { doc: IDPDocument }) {
  const hasImg = doc.has_images && doc.pages > 0
  return (
    <div className="p-6 space-y-5 max-w-4xl mx-auto">
      {hasImg && (
        <div className="rounded-xl overflow-hidden border border-[var(--border)] bg-black/40 shadow-lg">
          <img src={pageImageUrl(doc.id, 1)} alt="Page 1"
            className="w-full max-h-96 object-contain" />
        </div>
      )}
      {doc.text && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-5">
          <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-2.5 font-medium">Extracted text</div>
          <pre className="text-sm text-[var(--text-primary)] whitespace-pre-wrap font-sans leading-relaxed max-h-96 overflow-y-auto">
            {doc.text.slice(0, 4000)}
            {doc.text.length > 4000 && '\n…'}
          </pre>
        </div>
      )}
      {!hasImg && !doc.text && (
        <div className="text-sm text-[var(--text-muted)] text-center py-10">No preview available</div>
      )}
    </div>
  )
}

// ── Generic action tab (one-button operations) ──────────────────────
function ActionTab({ doc, run, label, hint }: { doc: IDPDocument; run: (d: IDPDocument) => Promise<{ text: string; model: string }>; label: string; hint: string }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ text: string; model: string } | null>(null)
  const [err, setErr] = useState<string | null>(null)

  async function go() {
    setBusy(true); setErr(null)
    try {
      const r = await run(doc)
      setResult(r)
    } catch (e: any) {
      setErr(e.message ?? 'Failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="p-6 space-y-4 max-w-4xl mx-auto">
      <p className="text-sm text-[var(--text-secondary)]">{hint}</p>
      <button onClick={go} disabled={busy}
        className="w-full px-4 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium
          hover:bg-[var(--accent-hover)] transition-colors disabled:opacity-50 flex items-center justify-center gap-2">
        {busy && <motion.div className="w-3.5 h-3.5 rounded-full border border-white/30 border-t-white"
          animate={{ rotate: 360 }} transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }} />}
        {busy ? 'Working…' : label}
      </button>
      {err && <ErrorBox text={err} />}
      {result && <ResultBlock text={result.text} model={result.model} />}
    </div>
  )
}

function ErrorBox({ text }: { text: string }) {
  // Highlight ollama pull commands so they're easy to copy
  const pullMatch = text.match(/`?ollama pull ([^\s`]+)`?/)
  return (
    <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 space-y-3">
      <div className="flex items-start gap-2.5">
        <svg className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 2L1 21h22L12 2zm0 6l7.5 13H4.5L12 8zm-1 4v4h2v-4h-2zm0 6v2h2v-2h-2z" />
        </svg>
        <p className="text-sm text-amber-200 leading-relaxed flex-1">{text}</p>
      </div>
      {pullMatch && (
        <div className="flex items-center gap-2 pt-2 border-t border-amber-500/30">
          <code className="flex-1 text-xs font-mono text-amber-300 bg-black/30 px-2.5 py-1.5 rounded">
            ollama pull {pullMatch[1]}
          </code>
          <button
            onClick={() => navigator.clipboard.writeText(`ollama pull ${pullMatch[1]}`)}
            className="text-xs text-amber-400 hover:text-amber-300 px-2.5 py-1.5 rounded hover:bg-amber-500/10 transition-colors"
            title="Copy"
          >
            Copy
          </button>
        </div>
      )}
    </div>
  )
}

function ResultBlock({ text, model }: { text: string; model: string }) {
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-5">
      <div className="text-xs text-[var(--text-muted)] mb-2 font-mono">via {model}</div>
      <pre className="text-sm text-[var(--text-primary)] whitespace-pre-wrap font-sans leading-relaxed max-h-[32rem] overflow-y-auto">
        {text}
      </pre>
    </div>
  )
}

// ── Summarize ───────────────────────────────────────────────────────
function SummarizeTab({ doc }: { doc: IDPDocument }) {
  const [style, setStyle] = useState<'brief' | 'detailed' | 'bullets'>('brief')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ text: string; model: string } | null>(null)
  async function go() {
    setBusy(true)
    try { setResult(await idp.summarize(doc.id, style)) } finally { setBusy(false) }
  }
  return (
    <div className="p-6 space-y-4 max-w-4xl mx-auto">
      <div className="flex gap-2">
        {(['brief','detailed','bullets'] as const).map(s => (
          <button key={s} onClick={() => setStyle(s)}
            className={clsx(
              'flex-1 px-3 py-2 rounded-lg text-sm border transition-colors capitalize',
              style === s
                ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
            )}>{s}</button>
        ))}
      </div>
      <button onClick={go} disabled={busy}
        className="relative w-full px-4 py-2.5 rounded-lg text-white text-sm font-medium
          transition-all disabled:cursor-not-allowed overflow-hidden flex items-center justify-center gap-2"
        style={{
          background: busy
            ? 'linear-gradient(135deg, var(--accent-deep), var(--accent-mid))'
            : 'linear-gradient(135deg, var(--accent), var(--accent-deep))',
          boxShadow: busy
            ? 'inset 0 1px 0 rgba(255,255,255,0.1)'
            : '0 6px 18px -6px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.2)',
        }}>
        {busy ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>Summarising…</span>
            {/* subtle shimmer overlay */}
            <span
              className="absolute inset-0 pointer-events-none"
              style={{
                background:
                  'linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent)',
                backgroundSize: '200% 100%',
                animation: 'shimmer 1.6s linear infinite',
              }}
            />
          </>
        ) : (
          <>
            <Sparkles className="w-4 h-4" />
            <span>Summarise</span>
          </>
        )}
      </button>
      {result && <ResultBlock text={result.text} model={result.model} />}
    </div>
  )
}

// ── Q&A ─────────────────────────────────────────────────────────────
function QATab({ doc }: { doc: IDPDocument }) {
  const [q, setQ] = useState('')
  const [history, setHistory] = useState<{ q: string; a: string; model: string }[]>([])
  const [busy, setBusy] = useState(false)
  async function ask() {
    const question = q.trim()
    if (!question) return
    setBusy(true)
    try {
      const r = await idp.qa(doc.id, question)
      setHistory(h => [...h, { q: question, a: r.text, model: r.model }])
      setQ('')
    } finally { setBusy(false) }
  }
  return (
    <div className="p-6 space-y-4 flex flex-col h-full max-w-4xl mx-auto w-full">
      <div className="flex-1 overflow-y-auto space-y-4 pr-1">
        {history.length === 0 && (
          <div className="text-sm text-[var(--text-muted)] text-center py-10">Ask anything about this document.</div>
        )}
        {history.map((h, i) => (
          <div key={i} className="space-y-2">
            <div className="text-sm text-[var(--text-primary)] bg-[var(--accent-dim)] rounded-xl px-4 py-2.5 ml-8 leading-relaxed">
              <span className="font-medium">Q: </span>{h.q}
            </div>
            <div className="text-sm text-[var(--text-primary)] bg-[var(--bg-tertiary)] rounded-xl px-4 py-2.5 mr-8 leading-relaxed border border-[var(--border)]">
              {h.a}
            </div>
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <input value={q} onChange={e => setQ(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && ask()}
          placeholder="Ask about this document…"
          className="flex-1 px-3.5 py-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)]
            text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)]" />
        <button onClick={ask} disabled={busy || !q.trim()}
          className="px-5 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-50">
          {busy ? '…' : 'Ask'}
        </button>
      </div>
    </div>
  )
}

// ── Tables ──────────────────────────────────────────────────────────
function TablesTab({ doc }: { doc: IDPDocument }) {
  const [busy, setBusy] = useState(false)
  const [tables, setTables] = useState<{ title?: string; headers?: string[]; rows?: string[][] }[]>([])
  const [err, setErr] = useState<string | null>(null)
  async function go() {
    setBusy(true); setErr(null)
    try {
      const r = await idp.tables(doc.id)
      setTables(r.tables ?? [])
      if ((r.tables ?? []).length === 0) setErr('No tables found in this document.')
    } catch (e: any) { setErr(e.message ?? 'Failed') }
    finally { setBusy(false) }
  }
  return (
    <div className="p-6 space-y-4 max-w-4xl mx-auto">
      <p className="text-sm text-[var(--text-secondary)]">Extract tables into structured rows.</p>
      <button onClick={go} disabled={busy}
        className="w-full px-4 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-50">
        {busy ? 'Extracting…' : 'Extract Tables'}
      </button>
      {err && <div className="text-sm text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg px-4 py-2.5">{err}</div>}
      {tables.map((t, i) => (
        <div key={i} className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] overflow-hidden">
          {t.title && <div className="text-sm text-[var(--accent)] font-medium px-4 py-2.5 border-b border-[var(--border)] bg-[var(--bg-secondary)]">{t.title}</div>}
          <div className="overflow-x-auto max-h-96">
            <table className="text-sm w-full">
              {t.headers && (
                <thead className="bg-[var(--bg-secondary)] sticky top-0">
                  <tr>{t.headers.map((h, j) => <th key={j} className="px-3 py-2 text-left text-[var(--text-secondary)] font-medium">{h}</th>)}</tr>
                </thead>
              )}
              <tbody>
                {(t.rows ?? []).map((row, j) => (
                  <tr key={j} className="border-t border-[var(--border)]">
                    {row.map((c, k) => <td key={k} className="px-3 py-2 text-[var(--text-primary)]">{c}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  )
}

// ── Entities ────────────────────────────────────────────────────────
function EntitiesTab({ doc }: { doc: IDPDocument }) {
  const [busy, setBusy] = useState(false)
  const [entities, setEntities] = useState<Record<string, string[]> | null>(null)
  async function go() {
    setBusy(true)
    try { setEntities((await idp.entities(doc.id)).entities) } finally { setBusy(false) }
  }
  return (
    <div className="p-6 space-y-4 max-w-4xl mx-auto">
      <p className="text-sm text-[var(--text-secondary)]">Find dates, people, organizations, amounts, contacts, addresses.</p>
      <button onClick={go} disabled={busy}
        className="w-full px-4 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-50">
        {busy ? 'Extracting…' : 'Extract Entities'}
      </button>
      {entities && (
        <div className="space-y-3">
          {Object.entries(entities).map(([k, vals]) => vals && vals.length > 0 && (
            <div key={k} className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-4">
              <div className="text-xs text-[var(--accent)] uppercase tracking-wider mb-2.5 font-medium">{k.replace(/_/g, ' ')}</div>
              <div className="flex flex-wrap gap-1.5">
                {vals.map((v, i) => (
                  <span key={i} className="text-xs px-2 py-1 rounded-md bg-[var(--bg-secondary)] text-[var(--text-primary)] border border-[var(--border)]">
                    {v}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Translate ───────────────────────────────────────────────────────
function TranslateTab({ doc }: { doc: IDPDocument }) {
  const [lang, setLang] = useState('French')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ text: string; model: string } | null>(null)
  const LANGS = ['French', 'German', 'Spanish', 'Italian', 'Dutch', 'Japanese', 'Chinese', 'Portuguese', 'Russian', 'Arabic', 'English']
  async function go() {
    setBusy(true)
    try { setResult(await idp.translate(doc.id, lang)) } finally { setBusy(false) }
  }
  return (
    <div className="p-6 space-y-4 max-w-4xl mx-auto">
      <select value={lang} onChange={e => setLang(e.target.value)}
        className="w-full px-3.5 py-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]">
        {LANGS.map(l => <option key={l} value={l}>{l}</option>)}
      </select>
      <button onClick={go} disabled={busy}
        className="w-full px-4 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-50">
        {busy ? 'Translating…' : `Translate to ${lang}`}
      </button>
      {result && <ResultBlock text={result.text} model={result.model} />}
    </div>
  )
}

// ── Redact ──────────────────────────────────────────────────────────
function RedactTab({ doc }: { doc: IDPDocument }) {
  const [cats, setCats] = useState<string[]>(['people', 'emails', 'phone numbers'])
  const ALL = ['people', 'organizations', 'emails', 'phone numbers', 'addresses', 'dates', 'amounts', 'IDs', 'URLs']
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ text: string; model: string } | null>(null)
  function toggle(c: string) {
    setCats(p => p.includes(c) ? p.filter(x => x !== c) : [...p, c])
  }
  async function go() {
    setBusy(true)
    try { setResult(await idp.redact(doc.id, cats)) } finally { setBusy(false) }
  }
  return (
    <div className="p-6 space-y-4 max-w-4xl mx-auto">
      <p className="text-sm text-[var(--text-secondary)]">Categories to redact:</p>
      <div className="flex flex-wrap gap-2">
        {ALL.map(c => (
          <button key={c} onClick={() => toggle(c)}
            className={clsx(
              'px-3 py-1.5 rounded-full text-xs border transition-colors',
              cats.includes(c)
                ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
            )}>{c}</button>
        ))}
      </div>
      <button onClick={go} disabled={busy || cats.length === 0}
        className="w-full px-4 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-50">
        {busy ? 'Redacting…' : 'Apply Redaction'}
      </button>
      {result && <ResultBlock text={result.text} model={result.model} />}
    </div>
  )
}

// ── Humanize ────────────────────────────────────────────────────────
function HumanizeTab({ doc }: { doc: IDPDocument }) {
  type Tone = 'natural' | 'casual' | 'professional' | 'academic'
  type Intensity = 'light' | 'medium' | 'heavy'
  const [tone, setTone] = useState<Tone>('natural')
  const [intensity, setIntensity] = useState<Intensity>('medium')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ text: string; model: string } | null>(null)
  const [copied, setCopied] = useState(false)

  async function go() {
    setBusy(true)
    try { setResult(await idp.humanize(doc.id, tone, intensity)) }
    finally { setBusy(false) }
  }

  async function copy() {
    if (!result) return
    await navigator.clipboard.writeText(result.text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1400)
  }

  const TONES: { id: Tone; label: string }[] = [
    { id: 'natural',       label: 'Natural' },
    { id: 'casual',        label: 'Casual' },
    { id: 'professional',  label: 'Professional' },
    { id: 'academic',      label: 'Academic' },
  ]
  const INTENSITIES: { id: Intensity; label: string; hint: string }[] = [
    { id: 'light',  label: 'Light',  hint: 'Smooth AI tics' },
    { id: 'medium', label: 'Medium', hint: 'Rework sentences' },
    { id: 'heavy',  label: 'Heavy',  hint: 'Free rewrite' },
  ]

  return (
    <div className="p-6 space-y-5 max-w-4xl mx-auto">
      <div>
        <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
          Rewrite this document so it reads like a human wrote it — varied rhythm, natural phrasing, no telltale AI tics.
          Facts, numbers and quotes are preserved.
        </p>
      </div>

      <div>
        <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-2 font-medium">Tone</div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {TONES.map(t => (
            <button key={t.id} onClick={() => setTone(t.id)}
              className={clsx(
                'px-3 py-2 rounded-lg text-sm border transition-colors',
                tone === t.id
                  ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                  : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
              )}>{t.label}</button>
          ))}
        </div>
      </div>

      <div>
        <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-2 font-medium">Rewrite strength</div>
        <div className="grid grid-cols-3 gap-2">
          {INTENSITIES.map(i => (
            <button key={i.id} onClick={() => setIntensity(i.id)}
              className={clsx(
                'px-3 py-2.5 rounded-lg text-sm border transition-colors text-left',
                intensity === i.id
                  ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                  : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
              )}>
              <div className="font-medium">{i.label}</div>
              <div className="text-xs text-[var(--text-muted)] mt-0.5">{i.hint}</div>
            </button>
          ))}
        </div>
      </div>

      <button onClick={go} disabled={busy}
        className="relative w-full px-4 py-2.5 rounded-lg text-white text-sm font-medium
          transition-all disabled:cursor-not-allowed overflow-hidden flex items-center justify-center gap-2"
        style={{
          background: busy
            ? 'linear-gradient(135deg, var(--accent-deep), var(--accent-mid))'
            : 'linear-gradient(135deg, var(--accent), var(--accent-deep))',
          boxShadow: busy
            ? 'inset 0 1px 0 rgba(255,255,255,0.1)'
            : '0 6px 18px -6px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.2)',
        }}>
        {busy ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>Humanising…</span>
            <span
              className="absolute inset-0 pointer-events-none"
              style={{
                background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent)',
                backgroundSize: '200% 100%',
                animation: 'shimmer 1.6s linear infinite',
              }}
            />
          </>
        ) : (
          <>
            <Wand2 className="w-4 h-4" />
            <span>Humanise</span>
          </>
        )}
      </button>

      {result && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-5">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs text-[var(--text-muted)] font-mono">via {result.model}</div>
            <button onClick={copy}
              className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--accent)] px-2.5 py-1 rounded-md hover:bg-[var(--bg-secondary)] transition-colors">
              {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <pre className="text-sm text-[var(--text-primary)] whitespace-pre-wrap font-sans leading-relaxed max-h-[32rem] overflow-y-auto">
            {result.text}
          </pre>
        </div>
      )}
    </div>
  )
}


// ── Export ──────────────────────────────────────────────────────────
function ExportTab({ doc }: { doc: IDPDocument }) {
  const [busy, setBusy] = useState<string | null>(null)
  const FORMATS: { fmt: 'md' | 'txt' | 'pdf' | 'json' | 'xlsx' | 'csv'; label: string; hint: string }[] = [
    { fmt: 'md',   label: 'Markdown',     hint: 'Extracted text as .md' },
    { fmt: 'txt',  label: 'Plain text',   hint: 'Raw text only' },
    { fmt: 'pdf',  label: 'PDF',          hint: 'Re-rendered cleanly' },
    { fmt: 'json', label: 'JSON',         hint: 'Full structured data' },
    { fmt: 'xlsx', label: 'Excel',        hint: 'Tables extracted to sheets' },
    { fmt: 'csv',  label: 'CSV',          hint: 'First table as CSV' },
  ]
  async function go(fmt: typeof FORMATS[number]['fmt']) {
    setBusy(fmt)
    try {
      const blob = await exportDoc(doc.id, fmt)
      if (!blob) return
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = doc.filename.replace(/\.[^.]+$/, '') + '.' + fmt
      a.click()
      URL.revokeObjectURL(url)
    } finally { setBusy(null) }
  }
  return (
    <div className="p-6 space-y-2.5 max-w-4xl mx-auto">
      {FORMATS.map(f => (
        <button key={f.fmt} onClick={() => go(f.fmt)} disabled={busy === f.fmt}
          className="w-full flex items-center justify-between p-4 rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)]
            hover:border-[var(--accent)] hover:bg-[var(--accent-dim)] transition-colors disabled:opacity-50">
          <div className="text-left">
            <div className="text-sm font-medium text-[var(--text-primary)]">{f.label}</div>
            <div className="text-xs text-[var(--text-muted)] mt-0.5">{f.hint}</div>
          </div>
          {busy === f.fmt ? (
            <motion.div className="w-4 h-4 rounded-full border border-[var(--accent)] border-t-transparent"
              animate={{ rotate: 360 }} transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }} />
          ) : (
            <Download className="w-4 h-4 text-[var(--accent)]" />
          )}
        </button>
      ))}
    </div>
  )
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
