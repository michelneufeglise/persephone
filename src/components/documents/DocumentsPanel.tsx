import { useEffect, useState, useRef, useCallback } from 'react'
import { motion } from 'framer-motion'
import {
  Upload, FileText, Trash2, FileScan, Languages, Sparkles,
  Table as TableIcon, Tags, MessageCircle, Eye, Shield, Download,
  ChevronLeft, RefreshCw, Loader2, Wand2, Copy, Check,
  CheckSquare, Square, Layers, Send, ScanLine, ChevronDown, ChevronRight, Brain, StopCircle,
  Maximize2, Minimize2,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useAppStore } from '@/store/appStore'
import { ModelActivity } from '@/components/ui/ModelActivity'
import {
  listDocuments, uploadDocument, deleteDocument, getDocument,
  idp, exportDoc, pageImageUrl, multiQa, streamIdp, streamMultiQa,
  layaStatus,
} from '@/lib/idp'
import type { IDPDocument } from '@/types'
import type { IDPMultiResult, StreamDone } from '@/lib/idp'
import { RoutingGraph } from './RoutingGraph'
import { DocChat } from './DocChat'
import { RightPanel } from './RightPanel'
import { ResizeHandle } from './ResizeHandle'
import { useDocChat } from './useDocChat'
import { SelectedDocsStrip } from './SelectedDocsStrip'
import { ConversationsList } from './ConversationsList'
import { MultiDocTab } from './MultiDocTab'

type Tab = 'overview' | 'ocr' | 'summarize' | 'qa' | 'tables' | 'entities' | 'translate' | 'redact' | 'humanize' | 'export'
type Mode = 'chat' | 'tools'

// ── Streaming state hook ────────────────────────────────────────────────────
interface StreamState {
  running: boolean
  phase: string
  content: string
  thinking: string
  tokPerSec: number
  error: string
  model: string
  usedOcr: string[]
  sources: string[]
  chunksUsed: number
}
const EMPTY_STREAM: StreamState = {
  running: false, phase: '', content: '', thinking: '', tokPerSec: 0,
  error: '', model: '', usedOcr: [], sources: [], chunksUsed: 0,
}

function useModelStream() {
  const [s, setS] = useState<StreamState>(EMPTY_STREAM)
  const abortRef = useRef<AbortController | null>(null)

  const run = useCallback(async (
    starter: (h: import('@/lib/idp').StreamHandlers, signal: AbortSignal) => Promise<void>,
  ) => {
    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac
    setS({ ...EMPTY_STREAM, running: true, phase: 'start' })
    try {
      await starter({
        onPhase: p => setS(prev => ({
          ...prev,
          phase: p.phase,
          model: p.model ?? prev.model,
          usedOcr: p.used_ocr ?? prev.usedOcr,
          sources: p.sources ?? prev.sources,
          chunksUsed: p.chunks_used ?? prev.chunksUsed,
        })),
        onThinking: d => setS(prev => ({ ...prev, thinking: prev.thinking + d })),
        onContent: d => setS(prev => ({ ...prev, content: prev.content + d, phase: prev.phase === 'done' ? prev.phase : 'generating' })),
        onError: m => setS(prev => ({ ...prev, error: m, running: false })),
        onDone: (d: StreamDone) => setS(prev => ({
          ...prev,
          running: false,
          phase: 'done',
          tokPerSec: d.stats?.tok_per_s ?? prev.tokPerSec,
          usedOcr: d.used_ocr ?? prev.usedOcr,
          sources: d.sources ?? prev.sources,
          chunksUsed: d.chunks_used ?? prev.chunksUsed,
        })),
      }, ac.signal)
    } catch (e: any) {
      if (e?.name !== 'AbortError') setS(prev => ({ ...prev, error: e?.message ?? 'Failed', running: false }))
    } finally {
      setS(prev => ({ ...prev, running: false }))
    }
  }, [])

  const cancel = useCallback(() => { abortRef.current?.abort(); setS(prev => ({ ...prev, running: false })) }, [])
  const reset = useCallback(() => { abortRef.current?.abort(); setS(EMPTY_STREAM) }, [])
  return { ...s, run, cancel, reset }
}

function ThinkingBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  if (!text.trim()) return null
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-secondary)]/60 overflow-hidden">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-2 text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors">
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        <Brain className="w-3.5 h-3.5" />
        Thinking {open ? '' : '(tap to expand)'}
      </button>
      {open && (
        <pre className="text-xs text-[var(--text-muted)] whitespace-pre-wrap font-sans leading-relaxed px-3 pb-3 max-h-56 overflow-y-auto border-t border-[var(--border)] pt-2">
          {text}
        </pre>
      )}
    </div>
  )
}

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
  const [mode, setMode] = useState<Mode>('chat')
  const [uploading, setUploading] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([])
  const [selectedDocsRoles, setSelectedDocsRoles] = useState<Record<string, 'auto' | 'subject' | 'reference'>>({})
  const [leftCollapsed, setLeftCollapsed] = useState(false)
  const [rightCollapsedPref, setRightCollapsedPref] = useState<boolean | null>(null)
  const [rightAutoCollapsed, setRightAutoCollapsed] = useState(false)
  const [rightPanelWidth, setRightPanelWidth] = useState(340)
  const [flowDrawerOpen, setFlowDrawerOpen] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const mainCardRef = useRef<HTMLDivElement>(null)
  const userSavedLeftCollapsedRef = useRef<boolean | null>(null)
  const isDraggingRef = useRef(false)
  const [computedMaxWidth, setComputedMaxWidth] = useState(1000)
  const [effectiveRightWidth, setEffectiveRightWidth] = useState(340)
  // Shared inputs for multi-doc operations
  const [sharedQuestion, setSharedQuestion] = useState('')
  const [sharedTarget, setSharedTarget] = useState('French')
  const [sharedCategories, setSharedCategories] = useState<string[]>(['people', 'emails', 'phone numbers'])
  const [sharedTone, setSharedTone] = useState<'natural' | 'casual' | 'professional' | 'academic'>('natural')
  const [sharedIntensity, setSharedIntensity] = useState<'light' | 'medium' | 'heavy'>('medium')

  // Load persisted state from localStorage
  useEffect(() => {
    try {
      const saved = localStorage.getItem('persephone-docs-left')
      if (saved) {
        const value = JSON.parse(saved)
        setLeftCollapsed(value)
        userSavedLeftCollapsedRef.current = value
      }
    } catch {}
    try {
      const saved = localStorage.getItem('persephone-docs-right-collapsed')
      if (saved) {
        const value = JSON.parse(saved)
        setRightCollapsedPref(value)
      }
    } catch {}
    try {
      const saved = localStorage.getItem('persephone-docs-mode')
      if (saved) setMode(JSON.parse(saved))
    } catch {}
    try {
      const saved = localStorage.getItem('persephone-docs-tab')
      if (saved) setTab(JSON.parse(saved))
    } catch {}
    try {
      const saved = localStorage.getItem('persephone-docs-selected')
      if (saved) setSelectedDocIds(JSON.parse(saved))
    } catch {}
    try {
      const saved = localStorage.getItem('persephone-docs-right-width')
      if (saved) setRightPanelWidth(JSON.parse(saved))
    } catch {}
  }, [])

  // Auto-collapse/expand panels based on container width and compute max right panel width
  useEffect(() => {
    if (!containerRef.current) return

    const observer = new ResizeObserver((entries) => {
      const container = entries[0]?.target as HTMLDivElement
      if (!container) return

      const containerWidth = container.clientWidth
      const LEFT_LIBRARY_WIDTH = 288 // w-72
      const LEFT_COLLAPSED_WIDTH = 32 // p-2 + icon
      const GAP = 16 // gap-4
      const MIN_CENTER_WIDTH = 380
      const CARD_PADDING = 8 // padding on main card edges

      // Auto-collapse left library below ~900px (only if user hasn't manually collapsed)
      if (containerWidth < 900) {
        if (userSavedLeftCollapsedRef.current !== true) {
          setLeftCollapsed(true)
        }
      } else {
        if (userSavedLeftCollapsedRef.current === null) {
          setLeftCollapsed(false)
        }
      }

      // Compute max width for right panel based on available space
      if (mode === 'chat') {
        // Main card takes up containerWidth - 2 * GAP
        const mainCardWidth = containerWidth - GAP * 2

        // Left column takes up its width (collapsed or expanded)
        const leftWidth = leftCollapsed ? LEFT_COLLAPSED_WIDTH : LEFT_LIBRARY_WIDTH
        const leftWithGap = leftWidth + (leftCollapsed ? 0 : GAP)

        // Center column minimum is MIN_CENTER_WIDTH
        const minCenterWidth = MIN_CENTER_WIDTH

        // Available for right panel: mainCard - left - center - gaps
        const availableForRight = mainCardWidth - leftWithGap - minCenterWidth - GAP * 2

        // Clamp to reasonable bounds
        const maxRight = Math.max(300, Math.min(1000, availableForRight))
        setComputedMaxWidth(maxRight)

        // Calculate effective right panel width
        const effectiveWidth = Math.min(rightPanelWidth, maxRight)
        setEffectiveRightWidth(effectiveWidth)

        // Calculate available width for center column
        let usedWidth = GAP * 2 // gaps
        if (!leftCollapsed) {
          usedWidth += LEFT_LIBRARY_WIDTH + GAP
        } else {
          usedWidth += LEFT_COLLAPSED_WIDTH
        }
        usedWidth += effectiveWidth + GAP
        const availableForCenter = containerWidth - usedWidth

        // Set auto-collapse flag (separate from user preference)
        const shouldAutoCollapse = availableForCenter < MIN_CENTER_WIDTH
        setRightAutoCollapsed(shouldAutoCollapse)
      }
    })

    observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [mode, leftCollapsed, rightPanelWidth])

  // Save state to localStorage
  const saveLeftCollapsed = (collapsed: boolean) => {
    setLeftCollapsed(collapsed)
    userSavedLeftCollapsedRef.current = collapsed
    try {
      localStorage.setItem('persephone-docs-left', JSON.stringify(collapsed))
    } catch {}
  }

  const handleRightCollapsedChange = (collapsed: boolean) => {
    setRightCollapsedPref(collapsed)
    try {
      localStorage.setItem('persephone-docs-right-collapsed', JSON.stringify(collapsed))
    } catch {}
  }

  // Effective collapsed state: user preference takes priority, else use auto-collapse
  const rightCollapsed = rightCollapsedPref !== null ? rightCollapsedPref : rightAutoCollapsed

  const saveRightPanelWidth = (width: number) => {
    setRightPanelWidth(width)
    try {
      localStorage.setItem('persephone-docs-right-width', JSON.stringify(width))
    } catch {}
  }

  const saveMode = (m: Mode) => {
    setMode(m)
    try {
      localStorage.setItem('persephone-docs-mode', JSON.stringify(m))
    } catch {}
  }

  const saveTab = (t: Tab) => {
    setTab(t)
    try {
      localStorage.setItem('persephone-docs-tab', JSON.stringify(t))
    } catch {}
  }

  const saveSelectedDocs = (ids: string[]) => {
    setSelectedDocIds(ids)
    try {
      localStorage.setItem('persephone-docs-selected', JSON.stringify(ids))
    } catch {}
  }

  const refresh = useCallback(async () => {
    const allDocs = await listDocuments()
    setDocs(allDocs)
    // Drop deleted docs from selection
    setSelectedDocIds(prev => prev.filter(id => allDocs.some(d => d.id === id)))
  }, [])

  const chat = useDocChat({
    onDocsChanged: () => refresh(),
    onDocsUploaded: (docIds) => {
      saveSelectedDocs([...selectedDocIds, ...docIds])
    },
  })

  function toggleSelected(id: string) {
    const next = selectedDocIds.includes(id)
      ? selectedDocIds.filter(x => x !== id)
      : [...selectedDocIds, id]
    saveSelectedDocs(next)
  }

  function clearSelected() {
    saveSelectedDocs([])
  }

  function selectAllDocs() {
    saveSelectedDocs(docs.map(d => d.id))
  }

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
    saveSelectedDocs(selectedDocIds.filter(x => x !== id))
    await refresh()
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) handleUpload(file)
  }

  // Helper to render tab content with support for multi-doc operations
  function renderTabContent(
    tabId: Tab,
    doc: IDPDocument,
    runSignal: number,
    onRunComplete: () => void,
    shared?: { question?: string; target?: string; categories?: string[]; tone?: 'natural' | 'casual' | 'professional' | 'academic'; intensity?: 'light' | 'medium' | 'heavy' }
  ) {
    if (tabId === 'overview') {
      return <OverviewTab doc={doc} />
    } else if (tabId === 'ocr') {
      return <ActionTab doc={doc} run={d => idp.ocr(d.id)} label="Run OCR" hint="Reads images using your configured OCR model." runSignal={runSignal} onRunComplete={onRunComplete} />
    } else if (tabId === 'summarize') {
      return <SummarizeTab doc={doc} runSignal={runSignal} onRunComplete={onRunComplete} />
    } else if (tabId === 'qa') {
      return <QATab doc={doc} runSignal={runSignal} onRunComplete={onRunComplete} question={shared?.question} />
    } else if (tabId === 'tables') {
      return <TablesTab doc={doc} runSignal={runSignal} onRunComplete={onRunComplete} />
    } else if (tabId === 'entities') {
      return <EntitiesTab doc={doc} runSignal={runSignal} onRunComplete={onRunComplete} />
    } else if (tabId === 'translate') {
      return <TranslateTab doc={doc} runSignal={runSignal} onRunComplete={onRunComplete} target={shared?.target} />
    } else if (tabId === 'redact') {
      return <RedactTab doc={doc} runSignal={runSignal} onRunComplete={onRunComplete} categories={shared?.categories} />
    } else if (tabId === 'humanize') {
      return <HumanizeTab doc={doc} runSignal={runSignal} onRunComplete={onRunComplete} tone={shared?.tone} intensity={shared?.intensity} />
    } else if (tabId === 'export') {
      return <ExportTab doc={doc} />
    }
    return null
  }

  // Main three-column layout
  return (
    <div ref={containerRef} className="h-full flex gap-4 overflow-hidden">
      {/* MAIN CARD: PanelHeader + Library + Center Column */}
      <div className="flex-1 min-w-0 glass rounded-3xl overflow-hidden flex flex-col">
        <PanelHeader />

        <div className="flex-1 flex min-h-0 overflow-hidden">
          {/* LEFT COLUMN: Document library (collapsible) */}
          {!leftCollapsed && (
            <div className="flex flex-col w-72 flex-shrink-0 border-r border-[var(--border)] bg-[var(--bg-secondary)]/40 overflow-hidden">
            {/* Library header */}
            <div className="flex-shrink-0 px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
              <span className="text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider">
                Library ({docs.length})
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={refresh}
                  className="p-1.5 text-[var(--text-muted)] hover:text-[var(--accent)] rounded-md hover:bg-[var(--bg-tertiary)] transition-colors"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => saveLeftCollapsed(true)}
                  className="p-1.5 text-[var(--text-muted)] hover:text-[var(--accent)] rounded-md hover:bg-[var(--bg-tertiary)] transition-colors"
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {/* Upload zone */}
            <div
              onDrop={onDrop}
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onClick={() => fileRef.current?.click()}
              className={clsx(
                'mx-3 mt-3 mb-2 p-5 rounded-xl border-2 border-dashed cursor-pointer transition-all text-center bg-[var(--bg-secondary)]/40 text-xs',
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
                accept=".pdf,.docx,.doc,.xlsx,.csv,.txt,.md,.rtf,.pptx,.odt,.html,.htm,.json,.xml,.eml,.png,.jpg,.jpeg,.webp,.gif"
              />
              {uploading ? (
                <div className="flex flex-col items-center gap-2 text-[var(--accent)]">
                  <motion.div
                    className="w-6 h-6 rounded-full border border-[var(--accent)] border-t-transparent"
                    animate={{ rotate: 360 }}
                    transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }}
                  />
                  <span className="text-xs">Processing…</span>
                </div>
              ) : (
                <>
                  <Upload className="w-5 h-5 text-[var(--accent)] mx-auto mb-1.5" />
                  <p className="text-xs text-[var(--text-primary)] font-medium">Upload</p>
                </>
              )}
            </div>

            {/* Document list */}
            <div className="flex-1 overflow-y-auto space-y-1 px-2 pb-2" style={{ scrollbarWidth: 'thin' }}>
              {docs.length === 0 ? (
                <div className="text-center text-xs text-[var(--text-muted)] py-8">
                  No documents yet
                </div>
              ) : (
                <>
                  {selectedDocIds.length > 0 && (
                    <div className="flex items-center justify-between px-1 py-1.5 text-xs">
                      <span className="text-[var(--text-muted)]">{selectedDocIds.length} selected</span>
                      <button
                        onClick={clearSelected}
                        className="text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors text-xs"
                      >
                        Clear
                      </button>
                    </div>
                  )}
                  {docs.map(d => (
                    <DocLibItem
                      key={d.id}
                      doc={d}
                      checked={selectedDocIds.includes(d.id)}
                      onToggle={() => toggleSelected(d.id)}
                      onSelect={() => { setActiveDocId(d.id); saveSelectedDocs([d.id]) }}
                      onDelete={() => handleDelete(d.id)}
                    />
                  ))}
                </>
              )}
            </div>

            {/* Conversations section */}
            <ConversationsList
              conversations={chat.conversations}
              activeId={chat.activeId}
              onSwitchConversation={(id) => {
                chat.switchConversation(id)
                saveMode('chat')
              }}
              onNewConversation={() => {
                chat.newConversation()
                saveMode('chat')
              }}
              onDeleteConversation={chat.deleteConversation}
            />
          </div>
        )}

        {/* Collapse button for left column */}
        {leftCollapsed && (
          <button
            onClick={() => saveLeftCollapsed(false)}
            className="flex-shrink-0 w-8 py-4 text-[var(--text-muted)] hover:text-[var(--accent)] border-r border-[var(--border)] flex items-center justify-center"
            title="Show library"
          >
            <ChevronRight className="w-4 h-4" />
          </button>
        )}

        {/* CENTER COLUMN: Chat or Tools */}
        <div className="flex-1 flex flex-col min-w-[380px] min-h-0">
          {/* Mode switcher at top */}
          <div className="flex-shrink-0 px-4 py-3 border-b border-[var(--border)] flex items-center justify-between bg-[var(--bg-secondary)]/40">
            <div className="flex items-center gap-2 min-w-0 flex-1">
              {mode === 'tools' && activeDoc && (
                <>
                  <button
                    onClick={() => setActiveDocId(null)}
                    className="p-1.5 text-[var(--text-muted)] hover:text-[var(--accent)] rounded-md hover:bg-[var(--bg-tertiary)] transition-colors flex-shrink-0"
                    title="Back to library"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <FileText className="w-4 h-4 text-[var(--accent)] flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-[var(--text-primary)] truncate" title={activeDoc.filename}>
                      {activeDoc.filename}
                    </div>
                  </div>
                </>
              )}
            </div>

            <div className="flex items-center gap-2 ml-2">
              {/* Mode switcher segmented control */}
              <div className="flex gap-1" role="tablist">
                <button
                  onClick={() => saveMode('chat')}
                  role="tab"
                  aria-selected={mode === 'chat'}
                  className={clsx(
                    'px-3 py-1.5 rounded-md text-sm font-medium transition-colors',
                    mode === 'chat'
                      ? 'bg-[var(--accent-dim)] text-[var(--accent)]'
                      : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]'
                  )}
                >
                  Chat
                </button>
                <button
                  onClick={() => saveMode('tools')}
                  role="tab"
                  aria-selected={mode === 'tools'}
                  className={clsx(
                    'px-3 py-1.5 rounded-md text-sm font-medium transition-colors',
                    mode === 'tools'
                      ? 'bg-[var(--accent-dim)] text-[var(--accent)]'
                      : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]'
                  )}
                >
                  Tools
                </button>
              </div>

              {/* Flow toggle for mobile (chat mode only) */}
              {mode === 'chat' && (
                <button
                  onClick={() => setFlowDrawerOpen(!flowDrawerOpen)}
                  className="lg:hidden p-1.5 text-[var(--text-muted)] hover:text-[var(--accent)] rounded-md hover:bg-[var(--bg-tertiary)] transition-colors"
                  title="Show flow"
                >
                  <Maximize2 className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>

          {/* Content based on mode */}
          {mode === 'chat' ? (
            <DocChat
              chat={chat}
              selectedDocIds={selectedDocIds}
              selectedDocs={docs.filter(d => selectedDocIds.includes(d.id))}
              selectedDocsRoles={selectedDocsRoles}
              onSelectedDocsRoleChange={(docId, role) => {
                setSelectedDocsRoles(prev => ({ ...prev, [docId]: role }))
              }}
              onDeselect={(docId) => {
                saveSelectedDocs(selectedDocIds.filter(x => x !== docId))
              }}
              onClear={clearSelected}
            />
          ) : (
            <>
              {/* Tools mode content */}
              {/* Tab strip for tools */}
              <div
                className="flex-shrink-0 flex items-center px-4 py-2 border-b border-[var(--border)] gap-1 overflow-x-auto bg-[var(--bg-secondary)]/60"
                style={{ scrollbarWidth: 'none' }}
              >
                {TABS.map(t => {
                  const Icon = t.icon
                  return (
                    <button
                      key={t.id}
                      onClick={() => saveTab(t.id)}
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

              {/* Tools content */}
              {selectedDocIds.length === 0 && !activeDoc ? (
                <div className="flex-1 flex items-center justify-center">
                  <div className="text-center">
                    <div className="text-sm text-[var(--text-muted)] mb-2">Select a document to view tools</div>
                    <div className="text-xs text-[var(--text-muted)]">OCR, summarize, translate, and more</div>
                  </div>
                </div>
              ) : (
                <>
                  {selectedDocIds.length > 1 ? (
                    // Multi-doc view
                    <MultiDocTab
                      docs={docs.filter(d => selectedDocIds.includes(d.id))}
                      tab={tab}
                      renderTab={(doc, signal, onComplete, shared) => renderTabContent(tab, doc, signal, onComplete, shared)}
                      shared={{
                        question: sharedQuestion,
                        target: sharedTarget,
                        categories: sharedCategories,
                        tone: sharedTone,
                        intensity: sharedIntensity,
                      }}
                      onSharedChange={{
                        question: setSharedQuestion,
                        target: setSharedTarget,
                        categories: setSharedCategories,
                        tone: setSharedTone,
                        intensity: setSharedIntensity,
                      }}
                    />
                  ) : (
                    // Single doc view
                    <div className="flex-1 overflow-y-auto bg-[var(--bg-secondary)]/40" style={{ scrollbarWidth: 'thin' }}>
                      {activeDoc ? (
                        <>
                          {tab === 'overview' && <OverviewTab doc={activeDoc} />}
                          {tab === 'ocr' && <ActionTab doc={activeDoc} run={d => idp.ocr(d.id)} label="Run OCR" hint="Reads images using your configured OCR model." />}
                          {tab === 'summarize' && <SummarizeTab doc={activeDoc} />}
                          {tab === 'qa' && <QATab doc={activeDoc} />}
                          {tab === 'tables' && <TablesTab doc={activeDoc} />}
                          {tab === 'entities' && <EntitiesTab doc={activeDoc} />}
                          {tab === 'translate' && <TranslateTab doc={activeDoc} />}
                          {tab === 'redact' && <RedactTab doc={activeDoc} />}
                          {tab === 'humanize' && <HumanizeTab doc={activeDoc} />}
                          {tab === 'export' && <ExportTab doc={activeDoc} />}
                        </>
                      ) : null}
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
      </div>

      {/* RESIZE HANDLE: Between main and right cards (chat mode only, not collapsed) */}
      {mode === 'chat' && !rightCollapsed && (
        <ResizeHandle
          currentWidth={effectiveRightWidth}
          minWidth={300}
          maxWidth={computedMaxWidth}
          containerWidth={containerRef.current?.clientWidth ?? 1000}
          onWidthChange={saveRightPanelWidth}
        />
      )}

      {/* RIGHT CARD: Knowledge graph and model flow (chat mode only) */}
      {mode === 'chat' && !rightCollapsed && (
        <div className="glass rounded-3xl overflow-hidden flex flex-col flex-shrink-0" style={{ width: `${effectiveRightWidth}px` }}>
          <RightPanel
            tiles={chat.selectedTiles}
            running={chat.isGenerating}
            conversations={chat.conversations}
            currentConversationId={chat.activeId || ''}
            currentMessages={chat.messages}
            selectedMessageId={chat.selectedMessageId}
            onSelectMessage={chat.selectMessage}
            collapsed={rightCollapsed}
            onCollapsedChange={handleRightCollapsedChange}
          />
        </div>
      )}

      {/* COLLAPSED RIGHT CARD: Thin strip (chat mode only, collapsed) */}
      {mode === 'chat' && rightCollapsed && (
        <div className="glass rounded-2xl overflow-hidden flex flex-col flex-shrink-0 w-11">
          <RightPanel
            tiles={chat.selectedTiles}
            running={chat.isGenerating}
            conversations={chat.conversations}
            currentConversationId={chat.activeId || ''}
            currentMessages={chat.messages}
            selectedMessageId={chat.selectedMessageId}
            onSelectMessage={chat.selectMessage}
            collapsed={rightCollapsed}
            onCollapsedChange={handleRightCollapsedChange}
          />
        </div>
      )}

        {/* Flow drawer toggle for mobile (chat mode only) */}
        {mode === 'chat' && flowDrawerOpen && (
          <div className="fixed inset-0 lg:hidden bg-black/30 z-50" onClick={() => setFlowDrawerOpen(false)}>
            <motion.div
              initial={{ x: 340 }}
              animate={{ x: 0 }}
              exit={{ x: 340 }}
              className="absolute right-0 top-0 h-full w-80 glass rounded-3xl overflow-hidden flex flex-col border-l border-[var(--border)] m-4"
              onClick={e => e.stopPropagation()}
            >
              {/* Mobile drawer with two tabs (forceVisible to show on mobile) */}
              <RightPanel
                tiles={chat.selectedTiles}
                running={chat.isGenerating}
                conversations={chat.conversations}
                currentConversationId={chat.activeId || ''}
                currentMessages={chat.messages}
                selectedMessageId={chat.selectedMessageId}
                onSelectMessage={chat.selectMessage}
                collapsed={false}
                onCollapsedChange={() => {}}
                forceVisible={true}
              />
            </motion.div>
          </div>
        )}

        {/* Flow toggle button for mobile */}
        {mode === 'chat' && (
          <button
            onClick={() => setFlowDrawerOpen(!flowDrawerOpen)}
            className="lg:hidden fixed bottom-6 right-6 p-3 rounded-full bg-[var(--accent)] text-white shadow-lg hover:bg-[var(--accent-hover)] transition-colors"
            title="Show flow"
          >
            <Maximize2 className="w-5 h-5" />
          </button>
        )}
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
function DocLibItem({ doc, checked, onToggle, onSelect, onDelete }: {
  doc: IDPDocument; checked: boolean; onToggle: () => void; onSelect: () => void; onDelete: () => void
}) {
  return (
    <div
      className={clsx(
        'group flex items-center gap-2.5 px-3 py-2.5 rounded-lg bg-[var(--bg-tertiary)]/40 hover:bg-[var(--bg-tertiary)] border transition-colors',
        checked ? 'border-[var(--accent)]' : 'border-transparent hover:border-[var(--border)]',
      )}
    >
      <button
        onClick={e => { e.stopPropagation(); onToggle() }}
        className="flex-shrink-0 text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
        title={checked ? 'Deselect' : 'Select for multi-doc query'}
      >
        {checked ? <CheckSquare className="w-4 h-4 text-[var(--accent)]" /> : <Square className="w-4 h-4" />}
      </button>
      <div onClick={onSelect} className="flex items-center gap-3 flex-1 min-w-0 cursor-pointer">
        <FileText className="w-4 h-4 text-[var(--accent)] flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-[var(--text-primary)] truncate" title={doc.filename}>
            {doc.filename}
          </div>
          <div className="text-xs text-[var(--text-muted)] mt-0.5">
            {doc.pages} page{doc.pages === 1 ? '' : 's'} · {formatBytes(doc.size)}
          </div>
        </div>
      </div>
      <button
        onClick={e => { e.stopPropagation(); onDelete() }}
        className="opacity-0 group-hover:opacity-100 p-1.5 text-[var(--text-muted)] hover:text-red-400 rounded-md hover:bg-red-500/10 transition-all flex-shrink-0"
      >
        <Trash2 className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}

function MultiDocChat({ docIds, docs }: { docIds: string[]; docs: IDPDocument[] }) {
  const [q, setQ] = useState('')
  const [autoOcr, setAutoOcr] = useState(true)
  const [history, setHistory] = useState<{ q: string; res: { text: string; model: string; used_ocr: string[]; sources: string[]; chunks_used: number } }[]>([])
  const st = useModelStream()
  const inFlightRef = useRef<string | null>(null)

  const enoughDocs = docIds.length >= 2

  async function ask() {
    const question = q.trim()
    if (!question || !enoughDocs) return
    inFlightRef.current = question
    st.run((h, signal) => streamMultiQa(docIds, question, autoOcr, h, signal))
  }

  // When streaming done, append to history
  useEffect(() => {
    if (st.phase === 'done' && inFlightRef.current && st.content) {
      setHistory(h => [...h, {
        q: inFlightRef.current!,
        res: { text: st.content, model: st.model, used_ocr: st.usedOcr, sources: st.sources, chunks_used: st.chunksUsed }
      }])
      setQ('')
      inFlightRef.current = null
      st.reset()
    }
  }, [st.phase])

  return (
    <div className="h-full flex flex-col rounded-2xl border border-[var(--border)] bg-[var(--bg-secondary)]/50 overflow-hidden">
      <div className="px-4 py-3 border-b border-[var(--border)] bg-[var(--bg-glass-strong)] flex items-center gap-2.5">
        <Layers className="w-4 h-4 text-[var(--accent)]" />
        <span className="text-sm font-medium text-[var(--text-primary)]">Query across documents</span>
        {docIds.length > 0 && (
          <span className="ml-auto text-xs text-[var(--text-muted)]">{docIds.length} selected</span>
        )}
      </div>

      {!enoughDocs ? (
        <div className="flex-1 flex items-center justify-center p-8 text-center">
          <p className="text-sm text-[var(--text-muted)] leading-relaxed">
            Select <strong className="text-[var(--text-secondary)]">2 or more</strong> documents (checkboxes on the left)
            to ask a question across all of them at once.
          </p>
        </div>
      ) : (
        <>
          <div className="flex-1 overflow-y-auto p-4 space-y-4" style={{ scrollbarWidth: 'thin' }}>
            {history.length === 0 && !st.running && (
              <div className="text-sm text-[var(--text-muted)] text-center py-8">
                Ask anything across the {docIds.length} selected documents.
              </div>
            )}
            {history.map((h, i) => (
              <div key={i} className="space-y-2">
                <div className="text-sm text-[var(--text-primary)] bg-[var(--accent-dim)] rounded-xl px-4 py-2.5 ml-6 leading-relaxed">
                  <span className="font-medium">Q: </span>{h.q}
                </div>
                <div className="text-sm text-[var(--text-primary)] bg-[var(--bg-tertiary)] rounded-xl px-4 py-2.5 mr-6 leading-relaxed border border-[var(--border)] space-y-2">
                  <div className="whitespace-pre-wrap">{h.res.text}</div>
                  <div className="text-[11px] text-[var(--text-muted)] font-mono pt-1.5 border-t border-[var(--border)] flex flex-wrap gap-x-3 gap-y-1">
                    <span>via {h.res.model || 'auto'}</span>
                    <span>· {h.res.chunks_used} passage{h.res.chunks_used === 1 ? '' : 's'}</span>
                    <span>· {h.res.sources.length} doc{h.res.sources.length === 1 ? '' : 's'}</span>
                    {h.res.used_ocr.length > 0 && (
                      <span className="text-[var(--accent)]">· OCR'd: {h.res.used_ocr.join(', ')}</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
            {st.running && inFlightRef.current && (
              <div className="space-y-2">
                <div className="text-sm text-[var(--text-primary)] bg-[var(--accent-dim)] rounded-xl px-4 py-2.5 ml-6 leading-relaxed">
                  <span className="font-medium">Q: </span>{inFlightRef.current}
                </div>
                <div className="space-y-3 mr-6">
                  <ModelActivity running={st.running} phase={st.phase} tokPerSec={st.tokPerSec} />
                  <ThinkingBlock text={st.thinking} />
                  {st.content && (
                    <div className="text-sm text-[var(--text-primary)] bg-[var(--bg-tertiary)] rounded-xl px-4 py-2.5 leading-relaxed border border-[var(--border)] whitespace-pre-wrap">
                      {st.content}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          {st.error && (
            <div className="mx-4 mb-2 text-sm text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">
              {st.error}
            </div>
          )}

          <div className="p-3 border-t border-[var(--border)] space-y-2.5">
            <button
              onClick={() => setAutoOcr(v => !v)}
              className={clsx(
                'flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-md border transition-colors',
                autoOcr
                  ? 'border-[var(--accent)] text-[var(--accent)] bg-[var(--accent-dim)]/40'
                  : 'border-[var(--border)] text-[var(--text-muted)] hover:border-[var(--border-bright)]',
              )}
              title="When on, image-only documents are OCR'd automatically before answering."
            >
              <ScanLine className="w-3.5 h-3.5" />
              Auto-OCR {autoOcr ? 'on' : 'off'}
            </button>
            <div className="flex gap-2">
              <input
                value={q}
                onChange={e => setQ(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && ask()}
                placeholder="Ask across selected documents…"
                className="flex-1 px-3.5 py-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)]"
              />
              <button
                onClick={st.running ? st.cancel : ask}
                disabled={!q.trim() && !st.running}
                className="px-4 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-50 flex items-center gap-1.5"
              >
                {st.running ? <><StopCircle className="w-4 h-4" /> Cancel</> : <><Send className="w-4 h-4" /> Send</>}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// ── Overview tab ────────────────────────────────────────────────────
function OverviewTab({ doc }: { doc: IDPDocument }) {
  const [routeLoaded, setRouteLoaded] = useState(false)
  const [layaReady, setLayaReady] = useState<boolean | null>(null)
  const hasImg = doc.has_images && doc.pages > 0

  useEffect(() => {
    if (!routeLoaded) {
      // Load Laya status once when tab is shown
      layaStatus().then(s => setLayaReady(s.available)).catch(() => setLayaReady(null))
      setRouteLoaded(true)
    }
  }, [routeLoaded])

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

      {/* Routing section */}
      <div className="space-y-2.5">
        <div>
          <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-2.5 font-medium">Routing</div>
          {layaReady === false && (
            <div className="text-xs text-[var(--text-muted)] bg-blue-500/10 border border-blue-500/40 rounded-lg px-3 py-2 mb-3">
              Decision model (Laya) not installed — using built-in rules
            </div>
          )}
          <RoutingGraph docId={doc.id} />
        </div>
      </div>
    </div>
  )
}

// ── Generic action tab (one-button operations) ──────────────────────
function ActionTab({ doc, run, label, hint, runSignal, onRunComplete }: { doc: IDPDocument; run: (d: IDPDocument) => Promise<{ text: string; model: string }>; label: string; hint: string; runSignal?: number; onRunComplete?: () => void }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ text: string; model: string } | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const lastSignalRef = useRef<number>(0)
  const triggeredBySignalRef = useRef(false)

  async function go() {
    setBusy(true); setErr(null)
    try {
      const r = await run(doc)
      setResult(r)
    } catch (e: any) {
      setErr(e.message ?? 'Failed')
    } finally {
      setBusy(false)
      if (triggeredBySignalRef.current && onRunComplete) {
        onRunComplete()
        triggeredBySignalRef.current = false
      }
    }
  }

  // Handle runSignal from multi-doc operations
  useEffect(() => {
    if (runSignal && runSignal > lastSignalRef.current) {
      lastSignalRef.current = runSignal
      triggeredBySignalRef.current = true
      go()
    }
  }, [runSignal])

  // Call onRunComplete on error if triggered by signal
  useEffect(() => {
    if (err && triggeredBySignalRef.current && onRunComplete) {
      onRunComplete()
      triggeredBySignalRef.current = false
    }
  }, [err])

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
      <ModelActivity running={busy} phase="working" />
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
function SummarizeTab({ doc, runSignal, onRunComplete }: { doc: IDPDocument; runSignal?: number; onRunComplete?: () => void }) {
  const [style, setStyle] = useState<'brief' | 'detailed' | 'bullets'>('brief')
  const st = useModelStream()
  const lastSignalRef = useRef<number>(0)

  function go() {
    st.run((h, signal) => streamIdp('summarize', doc.id, { style }, h, signal))
  }

  useEffect(() => {
    if (st.phase === 'done' && onRunComplete) {
      onRunComplete()
      st.reset()
    }
  }, [st.phase])

  useEffect(() => {
    if (st.error && onRunComplete) {
      onRunComplete()
    }
  }, [st.error])

  useEffect(() => {
    if (runSignal && runSignal > lastSignalRef.current) {
      lastSignalRef.current = runSignal
      go()
    }
  }, [runSignal])

  return (
    <div className="p-6 space-y-4 max-w-4xl mx-auto">
      <div className="flex gap-2">
        {(['brief','detailed','bullets'] as const).map(s => (
          <button key={s} onClick={() => setStyle(s)} disabled={st.running}
            className={clsx('flex-1 px-3 py-2 rounded-lg text-sm border transition-colors capitalize',
              style === s
                ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]')}>{s}</button>
        ))}
      </div>
      <button onClick={st.running ? st.cancel : go}
        className="w-full px-4 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] transition-colors flex items-center justify-center gap-2">
        {st.running ? <><StopCircle className="w-4 h-4" /> Cancel</> : <><Sparkles className="w-4 h-4" /> Summarise</>}
      </button>
      <ModelActivity running={st.running} phase={st.phase} tokPerSec={st.tokPerSec} />
      <ThinkingBlock text={st.thinking} />
      {st.error && <ErrorBox text={st.error} />}
      {st.content && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-5">
          {st.model && <div className="text-xs text-[var(--text-muted)] mb-2 font-mono">via {st.model}</div>}
          <pre className="text-sm text-[var(--text-primary)] whitespace-pre-wrap font-sans leading-relaxed max-h-[32rem] overflow-y-auto">{st.content}</pre>
        </div>
      )}
    </div>
  )
}

// ── Q&A ─────────────────────────────────────────────────────────────
function QATab({ doc, runSignal, onRunComplete, question: overrideQuestion }: { doc: IDPDocument; runSignal?: number; onRunComplete?: () => void; question?: string }) {
  const [q, setQ] = useState('')
  const [history, setHistory] = useState<{ q: string; a: string; model: string }[]>([])
  const st = useModelStream()
  const inFlightRef = useRef<string | null>(null)
  const lastSignalRef = useRef<number>(0)

  async function ask() {
    const question = overrideQuestion !== undefined ? overrideQuestion : q.trim()
    if (!question) return
    inFlightRef.current = question
    st.run((h, signal) => streamIdp('qa', doc.id, { question }, h, signal))
  }

  // When streaming done, append to history
  useEffect(() => {
    if (st.phase === 'done' && inFlightRef.current && st.content) {
      setHistory(h => [...h, { q: inFlightRef.current!, a: st.content, model: st.model }])
      if (overrideQuestion === undefined) setQ('')
      inFlightRef.current = null
      if (onRunComplete) onRunComplete()
      st.reset()
    }
  }, [st.phase])

  // Handle runSignal from multi-doc operations
  useEffect(() => {
    if (runSignal && runSignal > lastSignalRef.current) {
      lastSignalRef.current = runSignal
      ask()
    }
  }, [runSignal])

  // Call onRunComplete on error
  useEffect(() => {
    if (st.error && onRunComplete) {
      onRunComplete()
    }
  }, [st.error])

  return (
    <div className="p-6 space-y-4 flex flex-col h-full max-w-4xl mx-auto w-full">
      <div className="flex-1 overflow-y-auto space-y-4 pr-1">
        {history.length === 0 && !st.running && (
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
        {st.running && inFlightRef.current && (
          <div className="space-y-2">
            <div className="text-sm text-[var(--text-primary)] bg-[var(--accent-dim)] rounded-xl px-4 py-2.5 ml-8 leading-relaxed">
              <span className="font-medium">Q: </span>{inFlightRef.current}
            </div>
            <div className="space-y-3 mr-8">
              <ModelActivity running={st.running} phase={st.phase} tokPerSec={st.tokPerSec} />
              <ThinkingBlock text={st.thinking} />
              {st.content && (
                <div className="text-sm text-[var(--text-primary)] bg-[var(--bg-tertiary)] rounded-xl px-4 py-2.5 leading-relaxed border border-[var(--border)] whitespace-pre-wrap">
                  {st.content}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      {st.error && <ErrorBox text={st.error} />}
      {overrideQuestion === undefined && (
        <div className="flex gap-2">
          <input value={q} onChange={e => setQ(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && ask()}
            placeholder="Ask about this document…"
            className="flex-1 px-3.5 py-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)]
              text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)]" />
          <button onClick={st.running ? st.cancel : ask} disabled={!q.trim() && !st.running}
            className="px-5 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-50 flex items-center gap-1.5">
            {st.running ? <><StopCircle className="w-4 h-4" /> Cancel</> : <>Ask</>}
          </button>
        </div>
      )}
    </div>
  )
}

// ── Tables ──────────────────────────────────────────────────────────
function TablesTab({ doc, runSignal, onRunComplete }: { doc: IDPDocument; runSignal?: number; onRunComplete?: () => void }) {
  const [busy, setBusy] = useState(false)
  const [tables, setTables] = useState<{ title?: string; headers?: string[]; rows?: string[][] }[]>([])
  const [err, setErr] = useState<string | null>(null)
  const lastSignalRef = useRef<number>(0)
  const triggeredBySignalRef = useRef(false)

  async function go() {
    setBusy(true); setErr(null)
    try {
      const r = await idp.tables(doc.id)
      setTables(r.tables ?? [])
      if ((r.tables ?? []).length === 0) setErr('No tables found in this document.')
    } catch (e: any) { setErr(e.message ?? 'Failed') }
    finally {
      setBusy(false)
      if (triggeredBySignalRef.current && onRunComplete) {
        onRunComplete()
        triggeredBySignalRef.current = false
      }
    }
  }

  // Handle runSignal from multi-doc operations
  useEffect(() => {
    if (runSignal && runSignal > lastSignalRef.current) {
      lastSignalRef.current = runSignal
      triggeredBySignalRef.current = true
      go()
    }
  }, [runSignal])

  // Call onRunComplete on error if triggered by signal
  useEffect(() => {
    if (err && triggeredBySignalRef.current && onRunComplete) {
      onRunComplete()
      triggeredBySignalRef.current = false
    }
  }, [err])

  return (
    <div className="p-6 space-y-4 max-w-4xl mx-auto">
      <p className="text-sm text-[var(--text-secondary)]">Extract tables into structured rows.</p>
      <button onClick={go} disabled={busy}
        className="w-full px-4 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-50">
        {busy ? 'Extracting…' : 'Extract Tables'}
      </button>
      <ModelActivity running={busy} phase="working" />
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
function EntitiesTab({ doc, runSignal, onRunComplete }: { doc: IDPDocument; runSignal?: number; onRunComplete?: () => void }) {
  const [busy, setBusy] = useState(false)
  const [entities, setEntities] = useState<Record<string, string[]> | null>(null)
  const lastSignalRef = useRef<number>(0)
  const triggeredBySignalRef = useRef(false)

  async function go() {
    setBusy(true)
    try {
      setEntities((await idp.entities(doc.id)).entities)
    } finally {
      setBusy(false)
      if (triggeredBySignalRef.current && onRunComplete) {
        onRunComplete()
        triggeredBySignalRef.current = false
      }
    }
  }

  // Handle runSignal from multi-doc operations
  useEffect(() => {
    if (runSignal && runSignal > lastSignalRef.current) {
      lastSignalRef.current = runSignal
      triggeredBySignalRef.current = true
      go()
    }
  }, [runSignal])

  return (
    <div className="p-6 space-y-4 max-w-4xl mx-auto">
      <p className="text-sm text-[var(--text-secondary)]">Find dates, people, organizations, amounts, contacts, addresses.</p>
      <button onClick={go} disabled={busy}
        className="w-full px-4 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-50">
        {busy ? 'Extracting…' : 'Extract Entities'}
      </button>
      <ModelActivity running={busy} phase="working" />
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
function TranslateTab({ doc, runSignal, onRunComplete, target: overrideTarget }: { doc: IDPDocument; runSignal?: number; onRunComplete?: () => void; target?: string }) {
  const [lang, setLang] = useState('French')
  const st = useModelStream()
  const LANGS = ['French', 'German', 'Spanish', 'Italian', 'Dutch', 'Japanese', 'Chinese', 'Portuguese', 'Russian', 'Arabic', 'English']
  const lastSignalRef = useRef<number>(0)

  function go() {
    const targetLang = overrideTarget !== undefined ? overrideTarget : lang
    st.run((h, signal) => streamIdp('translate', doc.id, { target: targetLang }, h, signal))
  }

  useEffect(() => {
    if (st.phase === 'done' && onRunComplete) {
      onRunComplete()
      st.reset()
    }
  }, [st.phase])

  useEffect(() => {
    if (st.error && onRunComplete) {
      onRunComplete()
    }
  }, [st.error])

  useEffect(() => {
    if (runSignal && runSignal > lastSignalRef.current) {
      lastSignalRef.current = runSignal
      go()
    }
  }, [runSignal])

  return (
    <div className="p-6 space-y-4 max-w-4xl mx-auto">
      {overrideTarget === undefined && (
        <select value={lang} onChange={e => setLang(e.target.value)} disabled={st.running}
          className="w-full px-3.5 py-2.5 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)] disabled:opacity-50">
          {LANGS.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
      )}
      {overrideTarget !== undefined && (
        <div className="text-sm text-[var(--text-secondary)]">Target language: <strong>{overrideTarget}</strong></div>
      )}
      <button onClick={st.running ? st.cancel : go}
        className="w-full px-4 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] transition-colors flex items-center justify-center gap-2">
        {st.running ? <><StopCircle className="w-4 h-4" /> Cancel</> : <>Translate to {overrideTarget ?? lang}</>}
      </button>
      <ModelActivity running={st.running} phase={st.phase} tokPerSec={st.tokPerSec} />
      <ThinkingBlock text={st.thinking} />
      {st.error && <ErrorBox text={st.error} />}
      {st.content && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-5">
          {st.model && <div className="text-xs text-[var(--text-muted)] mb-2 font-mono">via {st.model}</div>}
          <pre className="text-sm text-[var(--text-primary)] whitespace-pre-wrap font-sans leading-relaxed max-h-[32rem] overflow-y-auto">{st.content}</pre>
        </div>
      )}
    </div>
  )
}

// ── Redact ──────────────────────────────────────────────────────────
function RedactTab({ doc, runSignal, onRunComplete, categories: overrideCategories }: { doc: IDPDocument; runSignal?: number; onRunComplete?: () => void; categories?: string[] }) {
  const [cats, setCats] = useState<string[]>(['people', 'emails', 'phone numbers'])
  const ALL = ['people', 'organizations', 'emails', 'phone numbers', 'addresses', 'dates', 'amounts', 'IDs', 'URLs']
  const st = useModelStream()
  const lastSignalRef = useRef<number>(0)

  function toggle(c: string) {
    setCats(p => p.includes(c) ? p.filter(x => x !== c) : [...p, c])
  }
  function go() {
    const categoriesToUse = overrideCategories !== undefined ? overrideCategories : cats
    st.run((h, signal) => streamIdp('redact', doc.id, { categories: categoriesToUse }, h, signal))
  }

  useEffect(() => {
    if (st.phase === 'done' && onRunComplete) {
      onRunComplete()
      st.reset()
    }
  }, [st.phase])

  useEffect(() => {
    if (st.error && onRunComplete) {
      onRunComplete()
    }
  }, [st.error])

  useEffect(() => {
    if (runSignal && runSignal > lastSignalRef.current) {
      lastSignalRef.current = runSignal
      go()
    }
  }, [runSignal])

  return (
    <div className="p-6 space-y-4 max-w-4xl mx-auto">
      <p className="text-sm text-[var(--text-secondary)]">Categories to redact:</p>
      {overrideCategories === undefined && (
        <div className="flex flex-wrap gap-2">
          {ALL.map(c => (
            <button key={c} onClick={() => toggle(c)} disabled={st.running}
              className={clsx(
                'px-3 py-1.5 rounded-full text-xs border transition-colors disabled:opacity-50',
                cats.includes(c)
                  ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                  : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
              )}>{c}</button>
          ))}
        </div>
      )}
      {overrideCategories !== undefined && (
        <div className="flex flex-wrap gap-2">
          {overrideCategories.map(c => (
            <span key={c} className="px-3 py-1.5 rounded-full text-xs border border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]">{c}</span>
          ))}
        </div>
      )}
      <button onClick={st.running ? st.cancel : go} disabled={(overrideCategories === undefined ? cats.length === 0 : overrideCategories.length === 0) && !st.running}
        className="w-full px-4 py-2.5 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] transition-colors flex items-center justify-center gap-2">
        {st.running ? <><StopCircle className="w-4 h-4" /> Cancel</> : <>Apply Redaction</>}
      </button>
      <ModelActivity running={st.running} phase={st.phase} tokPerSec={st.tokPerSec} />
      <ThinkingBlock text={st.thinking} />
      {st.error && <ErrorBox text={st.error} />}
      {st.content && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-5">
          {st.model && <div className="text-xs text-[var(--text-muted)] mb-2 font-mono">via {st.model}</div>}
          <pre className="text-sm text-[var(--text-primary)] whitespace-pre-wrap font-sans leading-relaxed max-h-[32rem] overflow-y-auto">{st.content}</pre>
        </div>
      )}
    </div>
  )
}

// ── Humanize ────────────────────────────────────────────────────────
function HumanizeTab({ doc, runSignal, onRunComplete, tone: overrideTone, intensity: overrideIntensity }: { doc: IDPDocument; runSignal?: number; onRunComplete?: () => void; tone?: 'natural' | 'casual' | 'professional' | 'academic'; intensity?: 'light' | 'medium' | 'heavy' }) {
  type Tone = 'natural' | 'casual' | 'professional' | 'academic'
  type Intensity = 'light' | 'medium' | 'heavy'
  const [tone, setTone] = useState<Tone>('natural')
  const [intensity, setIntensity] = useState<Intensity>('medium')
  const st = useModelStream()
  const [copied, setCopied] = useState(false)
  const lastSignalRef = useRef<number>(0)

  function go() {
    const toneToUse = overrideTone !== undefined ? overrideTone : tone
    const intensityToUse = overrideIntensity !== undefined ? overrideIntensity : intensity
    st.run((h, signal) => streamIdp('humanize', doc.id, { tone: toneToUse, intensity: intensityToUse }, h, signal))
  }

  async function copy() {
    if (!st.content) return
    await navigator.clipboard.writeText(st.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 1400)
  }

  useEffect(() => {
    if (st.phase === 'done' && onRunComplete) {
      onRunComplete()
      st.reset()
    }
  }, [st.phase])

  useEffect(() => {
    if (st.error && onRunComplete) {
      onRunComplete()
    }
  }, [st.error])

  useEffect(() => {
    if (runSignal && runSignal > lastSignalRef.current) {
      lastSignalRef.current = runSignal
      go()
    }
  }, [runSignal])

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
        {overrideTone === undefined ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {TONES.map(t => (
              <button key={t.id} onClick={() => setTone(t.id)} disabled={st.running}
                className={clsx(
                  'px-3 py-2 rounded-lg text-sm border transition-colors disabled:opacity-50',
                  tone === t.id
                    ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                    : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
                )}>{t.label}</button>
            ))}
          </div>
        ) : (
          <div className="text-sm text-[var(--text-secondary)]"><strong>{TONES.find(t => t.id === overrideTone)?.label}</strong></div>
        )}
      </div>

      <div>
        <div className="text-xs text-[var(--text-muted)] uppercase tracking-wider mb-2 font-medium">Rewrite strength</div>
        {overrideIntensity === undefined ? (
          <div className="grid grid-cols-3 gap-2">
            {INTENSITIES.map(i => (
              <button key={i.id} onClick={() => setIntensity(i.id)} disabled={st.running}
                className={clsx(
                  'px-3 py-2.5 rounded-lg text-sm border transition-colors text-left disabled:opacity-50',
                  intensity === i.id
                    ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                    : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
                )}>
                <div className="font-medium">{i.label}</div>
                <div className="text-xs text-[var(--text-muted)] mt-0.5">{i.hint}</div>
              </button>
            ))}
          </div>
        ) : (
          <div className="text-sm text-[var(--text-secondary)]"><strong>{INTENSITIES.find(i => i.id === overrideIntensity)?.label}</strong></div>
        )}
      </div>

      <button onClick={st.running ? st.cancel : go}
        className="relative w-full px-4 py-2.5 rounded-lg text-white text-sm font-medium
          transition-all overflow-hidden flex items-center justify-center gap-2"
        style={{
          background: st.running
            ? 'linear-gradient(135deg, var(--accent-deep), var(--accent-mid))'
            : 'linear-gradient(135deg, var(--accent), var(--accent-deep))',
          boxShadow: st.running
            ? 'inset 0 1px 0 rgba(255,255,255,0.1)'
            : '0 6px 18px -6px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.2)',
        }}>
        {st.running ? (
          <>
            <StopCircle className="w-4 h-4" />
            <span>Cancel</span>
          </>
        ) : (
          <>
            <Wand2 className="w-4 h-4" />
            <span>Humanise</span>
          </>
        )}
      </button>

      <ModelActivity running={st.running} phase={st.phase} tokPerSec={st.tokPerSec} />
      <ThinkingBlock text={st.thinking} />
      {st.error && <ErrorBox text={st.error} />}
      {st.content && (
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-tertiary)] p-5">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs text-[var(--text-muted)] font-mono">via {st.model}</div>
            <button onClick={copy}
              className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--accent)] px-2.5 py-1 rounded-md hover:bg-[var(--bg-secondary)] transition-colors">
              {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <pre className="text-sm text-[var(--text-primary)] whitespace-pre-wrap font-sans leading-relaxed max-h-[32rem] overflow-y-auto">
            {st.content}
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
