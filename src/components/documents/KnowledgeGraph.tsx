import { createPortal } from 'react-dom'
import React, { useMemo, useState, useEffect, useCallback, memo, useRef } from 'react'
import {
  ReactFlow,
  Background,
  BackgroundVariant,
  Controls,
  useNodesState,
  useEdgesState,
  Node,
  Edge,
  Panel,
  MarkerType,
  useReactFlow,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { AlertCircle, X, ExternalLink, Menu, ChevronUp, ChevronDown, Maximize2, Lock } from 'lucide-react'
import { clsx } from 'clsx'
import { buildKnowledgeGraph, layoutKnowledgeGraph, NODE_SIZES } from './kgModel'
import { layoutForce } from './kgForce'
import { loadDocConversation, type DocConversationSummary, fetchKnowledgeGraph } from '@/lib/docAgent'
import { DocumentNodeComponent, QuestionNodeComponent, DecisionNodeComponent, ModelNodeComponent, DocumentNodeCompactComponent, QuestionNodeCompactComponent, DecisionNodeCompactComponent, ModelNodeCompactComponent, PlannerNodeComponent, WebNodeComponent, ProfileNodeComponent, PlannerNodeCompactComponent, WebNodeCompactComponent, ProfileNodeCompactComponent, EntityNodeComponent, PipelineNodeComponent, PipelineGroupNodeComponent } from './kgNodes'
import { FloatingEdge } from './kgFloatingEdge'
import { buildPipeline } from './kgPipeline'
import type { Message } from '@/types'

// Define edgeTypes outside component to avoid re-creation
const edgeTypesNetwork = { floating: FloatingEdge }
const edgeTypesLayers = {}

interface KnowledgeGraphProps {
  conversations: DocConversationSummary[]
  currentConversationId: string
  currentMessages: Message[]
  liveMessages?: Message[]
  selectedMessageId?: string | null
  onSelectMessage?: (assistantMessageId: string) => void
}

const nodeTypes = {
  document: DocumentNodeComponent,
  question: QuestionNodeComponent,
  decision: DecisionNodeComponent,
  model: ModelNodeComponent,
  documentCompact: DocumentNodeCompactComponent,
  questionCompact: QuestionNodeCompactComponent,
  decisionCompact: DecisionNodeCompactComponent,
  modelCompact: ModelNodeCompactComponent,
  planner: PlannerNodeComponent,
  web: WebNodeComponent,
  profile: ProfileNodeComponent,
  plannerCompact: PlannerNodeCompactComponent,
  webCompact: WebNodeCompactComponent,
  profileCompact: ProfileNodeCompactComponent,
  entity: EntityNodeComponent,
  pipeline: PipelineNodeComponent,
  pipelineGroup: PipelineGroupNodeComponent,
}

/**
 * ResizeObserver wrapper to fit graph on container resize
 */
function GraphResizeHandler({ containerRef, padding = 0.15, minZoom = 0.4 }: { containerRef: React.RefObject<HTMLDivElement>; padding?: number; minZoom?: number }) {
  const { fitView } = useReactFlow()

  useEffect(() => {
    if (!containerRef.current) return

    let timeoutId: NodeJS.Timeout
    const resizeObserver = new ResizeObserver(() => {
      clearTimeout(timeoutId)
      timeoutId = setTimeout(() => {
        fitView({ padding, minZoom, duration: 250 })
      }, 120)
    })

    resizeObserver.observe(containerRef.current)

    // Fit on mount
    const mountTimeout = setTimeout(() => {
      fitView({ padding, minZoom, duration: 250 })
    }, 50)

    return () => {
      clearTimeout(timeoutId)
      clearTimeout(mountTimeout)
      resizeObserver.disconnect()
    }
  }, [fitView, containerRef, padding, minZoom])

  return null
}

/**
 * Collapsible Legend component
 */
function Legend() {
  const [open, setOpen] = useState(false)

  return (
    <div className="bg-[var(--bg-glass-strong)] backdrop-blur border border-[var(--border-glass)] rounded-[12px] overflow-hidden shadow-[var(--shadow-soft)]">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
      >
        <span className="uppercase tracking-wider">Legend</span>
        {open ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />}
      </button>

      {open && (
        <div className="border-t border-[var(--border-glass)] px-3 py-2 space-y-2 text-[0.7rem] text-[var(--text-secondary)]">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-red-500/20 to-red-600/10 flex items-center justify-center text-sm">
              📄
            </div>
            <span>Document</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-amber-500/20 to-amber-600/10 flex items-center justify-center text-sm">
              💬
            </div>
            <span>Question</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-purple-500/20 to-purple-600/10 flex items-center justify-center text-sm font-bold">
              ✨
            </div>
            <span>Decision (Laya)</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-pink-500 to-pink-600 flex items-center justify-center text-sm">
              ⚙️
            </div>
            <span>Model</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-yellow-500/30 to-amber-500/20 flex items-center justify-center text-sm">
              📋
            </div>
            <span>Planner</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-cyan-400/30 to-blue-400/20 flex items-center justify-center text-sm">
              🌐
            </div>
            <span>Web</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-blue-500/30 to-blue-600/20 flex items-center justify-center text-sm">
              👤
            </div>
            <span>Profile</span>
          </div>
          <div className="border-t border-[var(--border-glass)] pt-2 mt-2">
            <div className="flex items-center gap-2">
              <div className="w-2 h-0.5 border-t-2 border-red-500/60 border-dashed" />
              <span>Failed / Fallback</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * Detail card for selected node
 */
function DetailCard({
  node,
  onClose,
  onSelectMessage,
}: {
  node: any
  onClose: () => void
  onSelectMessage?: (msgId: string) => void
}) {
  return (
    <div className="absolute bottom-2 left-2 right-2 bg-[var(--bg-glass-strong)] backdrop-blur border border-[var(--border-glass)] rounded-[12px] p-3 text-xs max-h-48 overflow-y-auto shadow-[var(--shadow-soft)] animate-in slide-in-from-bottom-2 duration-200">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="font-semibold text-[0.8rem] text-[var(--text-primary)]">{node.label}</div>
        <button
          onClick={onClose}
          className="flex-shrink-0 text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--glass-fill-hover)] rounded p-1 transition-colors"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {node.kind === 'question' && (
        <div className="space-y-2">
          {node.data.fullText && (
            <div>
              <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-1">Question</div>
              <div className="text-[0.75rem] text-[var(--text-secondary)] leading-snug">{node.data.fullText}</div>
            </div>
          )}
          {node.data.answer && (
            <div>
              <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-1">Answer</div>
              <div className="text-[0.75rem] text-[var(--text-secondary)] leading-snug">{node.data.answer.preview}</div>
              <div className="text-[0.65rem] text-[var(--text-muted)] mt-2 font-mono">
                {node.data.answer.model || '—'} • {node.data.answer.ms}ms
              </div>
            </div>
          )}
          {onSelectMessage && node.messageIds?.[1] && (
            <button
              onClick={() => onSelectMessage(node.messageIds[1])}
              className="text-[0.7rem] text-[var(--accent)] hover:text-[var(--accent-hover)] flex items-center gap-1 mt-2 transition-colors font-medium"
            >
              Show in chat
              <ExternalLink className="w-3 h-3" />
            </button>
          )}
        </div>
      )}

      {node.kind === 'decision' && (
        <div className="space-y-1.5">
          <div>
            <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-0.5">Value</div>
            <div className="text-[0.75rem] text-[var(--text-secondary)]">{node.data.value}</div>
          </div>
          {node.data.confidence !== null && (
            <div>
              <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-0.5">Confidence</div>
              <div className="text-[0.75rem] text-[var(--text-secondary)]">{Math.round(node.data.confidence * 100)}%</div>
            </div>
          )}
          {node.data.source && (
            <div>
              <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-0.5">Source</div>
              <div className="text-[0.75rem] text-[var(--text-secondary)] capitalize">{node.data.source}</div>
            </div>
          )}
          {node.data.note && (
            <div>
              <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-0.5">Note</div>
              <div className="text-[0.75rem] text-[var(--text-secondary)]">{node.data.note}</div>
            </div>
          )}
          {node.data.probabilities && Object.keys(node.data.probabilities).length > 0 && (
            <div>
              <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-1">Top matches</div>
              <div className="space-y-0.5">
                {Object.entries(node.data.probabilities)
                  .sort((a, b) => (b[1] as number) - (a[1] as number))
                  .slice(0, 3)
                  .map(([label, prob]) => (
                    <div key={label} className="text-[0.7rem] flex justify-between">
                      <span className="text-[var(--text-secondary)]">{label}</span>
                      <span className="text-[var(--text-muted)] font-mono">{Math.round((prob as number) * 100)}%</span>
                    </div>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      {node.kind === 'model' && (
        <div className="space-y-1.5">
          <div>
            <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-0.5">Model</div>
            <div className="text-[0.75rem] text-[var(--text-secondary)] font-mono">{node.data.modelName}</div>
          </div>
          {node.data.roles?.length > 0 && (
            <div>
              <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-1">Roles</div>
              <div className="flex flex-wrap gap-1">
                {node.data.roles.map((r: string) => (
                  <span key={r} className="px-2 py-1 rounded-full bg-[var(--accent-dim)] text-[0.65rem] text-[var(--accent)] font-semibold">
                    {r}
                  </span>
                ))}
              </div>
            </div>
          )}
          {node.data.useCount && (
            <div>
              <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-0.5">Uses</div>
              <div className="text-[0.75rem] text-[var(--text-secondary)]">×{node.data.useCount}</div>
            </div>
          )}
        </div>
      )}

      {node.kind === 'document' && (
        <div className="space-y-1.5">
          <div>
            <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-0.5">Document ID</div>
            <div className="text-[0.75rem] text-[var(--text-secondary)] font-mono text-[0.65rem]">{node.data.docId}</div>
          </div>
          {node.data.kind && (
            <div>
              <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-0.5">Kind</div>
              <div className="text-[0.75rem] text-[var(--text-secondary)] capitalize">{node.data.kind}</div>
            </div>
          )}
          {node.data.pages && (
            <div>
              <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-0.5">Pages</div>
              <div className="text-[0.75rem] text-[var(--text-secondary)]">{node.data.pages}</div>
            </div>
          )}
          {node.data.roles?.length > 0 && (
            <div>
              <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-1">Roles</div>
              <div className="flex flex-wrap gap-1">
                {node.data.roles.map((r: string) => (
                  <span key={r} className="px-2 py-1 rounded-full bg-[var(--accent-dim)] text-[0.65rem] text-[var(--accent)] font-semibold">
                    {r}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {node.kind === 'pipeline' && (
        <div className="space-y-1.5">
          <div className="text-[0.7rem] text-[var(--text-muted)] uppercase font-bold tracking-wider">
            {node.kindLabel}
            {typeof node.used === 'boolean' && ` · ${node.used ? 'used this run' : 'not used this run'}`}
          </div>
          {node.fullText && node.fullText !== node.label && (
            <div className="text-[0.75rem] text-[var(--text-secondary)] leading-snug whitespace-pre-wrap">{node.fullText}</div>
          )}
          {node.chips?.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {node.chips.map((c: string) => (
                <span key={c} className="px-2 py-0.5 rounded-full bg-[var(--accent-dim)] text-[0.65rem] text-[var(--accent)] font-semibold">
                  {c}
                </span>
              ))}
            </div>
          )}
          {node.details?.map((d: string, i: number) => (
            <div key={i} className="text-[0.72rem] text-[var(--text-secondary)]">{d}</div>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Expanded modal view
 */
const GraphModal = memo(
  ({
    isOpen,
    conversations,
    currentConversationId,
    currentMessages,
    liveMessages,
    selectedMessageId,
    onSelectMessage,
    onClose,
  }: {
    isOpen: boolean
    conversations: DocConversationSummary[]
    currentConversationId: string
    currentMessages: Message[]
    liveMessages?: Message[]
    selectedMessageId?: string | null
    onSelectMessage?: (assistantMessageId: string) => void
    onClose: () => void
  }) => {
    const closeButtonRef = React.useRef<HTMLButtonElement>(null)

    // Handle ESC key and focus close button
    React.useEffect(() => {
      if (!isOpen) return

      // Focus close button on open
      closeButtonRef.current?.focus()

      // Handle ESC key
      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === 'Escape') {
          onClose()
        }
      }

      window.addEventListener('keydown', handleKeyDown)
      return () => window.removeEventListener('keydown', handleKeyDown)
    }, [isOpen, onClose])

    if (!isOpen) return null

    // Portal to <body>: the glass card ancestor has backdrop-filter, which would
    // otherwise trap this fixed-position overlay inside the right panel.
    return createPortal(
      <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
        <div
          className="glass-strong rounded-3xl w-[90vw] h-[85vh] flex flex-col overflow-hidden"
          onClick={e => e.stopPropagation()}
        >
          <div className="border-b border-[var(--glass-stroke)] px-6 py-4 flex items-center justify-between">
            <h2 className="text-lg font-display font-bold text-[var(--text-primary)]">Knowledge Graph</h2>
            <button
              ref={closeButtonRef}
              onClick={onClose}
              className="text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--glass-fill-hover)] rounded-lg p-2 transition-colors"
              title="Close (ESC)"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
          <div className="flex-1">
            <KnowledgeGraphInner
              conversations={conversations}
              currentConversationId={currentConversationId}
              currentMessages={currentMessages}
              liveMessages={liveMessages}
              selectedMessageId={selectedMessageId}
              onSelectMessage={onSelectMessage}
              isExpanded
            />
          </div>
        </div>
      </div>
    , document.body)
  },
)

/**
 * Inner graph component with all the graph rendering logic
 */
const KnowledgeGraphInner = memo(
  ({
    conversations,
    currentConversationId,
    currentMessages,
    liveMessages,
    selectedMessageId,
    onSelectMessage,
    isExpanded = false,
    onExpand,
  }: {
    conversations: DocConversationSummary[]
    currentConversationId: string
    currentMessages: Message[]
    liveMessages?: Message[]
    selectedMessageId?: string | null
    onSelectMessage?: (assistantMessageId: string) => void
    isExpanded?: boolean
    onExpand?: () => void
  }) => {
    const [scope, setScope] = useState<'current' | 'all'>('current')
    const [loadedConversations, setLoadedConversations] = useState<
      { id: string; title: string; messages: Message[] }[]
    >([])
    const [loadingCount, setLoadingCount] = useState(0)
    const [selectedNode, setSelectedNode] = useState<any>(null)
    const [selectedKinds, setSelectedKinds] = useState<Set<string>>(new Set(['document', 'question', 'decision', 'model', 'planner', 'web', 'profile']))
    const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
    const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
    const [graphDirection, setGraphDirection] = useState<'LR' | 'TB'>(isExpanded ? 'LR' : 'TB')
    const [graphView, setGraphView] = useState<'pipeline' | 'network' | 'entities' | 'layers'>('pipeline')
    const [containerSize, setContainerSize] = useState<{ width: number; height: number }>({ width: 800, height: 600 })
    const [kgData, setKgData] = useState<any>(null)
    // Previous layout = bookkeeping for warm-starting the next force layout; refs, not
    // state, because it is written while computing the layout (state here would loop).
    const previousPositionsRef = useRef<Map<string, { x: number; y: number }>>(new Map())
    const previousVirtualSizeRef = useRef<{ width: number; height: number } | null>(null)
    const [zoomLevel, setZoomLevel] = useState(1)
    const [lodMode, setLodMode] = useState<'full' | 'compact'>('full')
    const [hoveredId, setHoveredId] = useState<string | null>(null)
    const pinnedRef = useRef<Map<string, { x: number; y: number }>>(new Map())
    const containerRef = useRef<HTMLDivElement | null>(null)

    // Load scope and view preferences from localStorage
    useEffect(() => {
      try {
        const saved = localStorage.getItem('persephone-docs-kg-scope')
        if (saved === 'all') setScope('all')
        const savedView = localStorage.getItem('persephone-docs-kg-view')
        if (savedView === 'pipeline' || savedView === 'network' || savedView === 'entities' || savedView === 'layers') {
          setGraphView(savedView as 'pipeline' | 'network' | 'entities' | 'layers')
        }

        // Load selectedKinds from localStorage and ensure new kinds are present
        const savedKinds = localStorage.getItem('persephone-docs-kg-selected-kinds')
        if (savedKinds) {
          try {
            const parsed = JSON.parse(savedKinds) as string[]
            const kinds = new Set(parsed)
            // Add new kinds if not present
            const requiredKinds = ['planner', 'web', 'profile']
            for (const kind of requiredKinds) {
              kinds.add(kind)
            }
            setSelectedKinds(kinds)
          } catch {
            // ignore parse error, use default
          }
        }
      } catch {
        // ignore
      }
    }, [])

    // Save scope preference
    useEffect(() => {
      try {
        localStorage.setItem('persephone-docs-kg-scope', scope)
      } catch {
        // ignore
      }
    }, [scope])

    // Save view preference
    useEffect(() => {
      try {
        localStorage.setItem('persephone-docs-kg-view', graphView)
      } catch {
        // ignore
      }
    }, [graphView])

    // Save selectedKinds preference
    useEffect(() => {
      try {
        localStorage.setItem('persephone-docs-kg-selected-kinds', JSON.stringify(Array.from(selectedKinds)))
      } catch {
        // ignore
      }
    }, [selectedKinds])

    // Load all conversations when switching to 'all' scope
    useEffect(() => {
      if (scope === 'all' && conversations.length > 0) {
        setLoadingCount(conversations.length)
        const load = async () => {
          const loaded: typeof loadedConversations = []
          for (const conv of conversations) {
            try {
              const msgs = await loadDocConversation(conv.id)
              loaded.push({
                id: conv.id,
                title: conv.title,
                messages: msgs,
              })
            } catch (e) {
              // skip on error
            } finally {
              setLoadingCount(prev => prev - 1)
            }
          }
          setLoadedConversations(loaded)
        }
        load()
      } else {
        setLoadedConversations([])
      }
    }, [scope, conversations])

    // Monitor container width and update graph direction adaptively.
    // The container mounts later than this component (empty/loading states
    // render first), so track it via a callback ref instead of a one-shot effect.
    const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null)
    const setContainerNode = useCallback((el: HTMLDivElement | null) => {
      containerRef.current = el
      setContainerEl(el)
    }, [])
    useEffect(() => {
      if (!containerEl) return
      let timeoutId: ReturnType<typeof setTimeout> | undefined
      let lastWidth = containerEl.clientWidth
      let lastHeight = containerEl.clientHeight

      const update = () => {
        const width = containerEl.clientWidth
        const height = containerEl.clientHeight

        // Update container size if changed by >40px
        if (Math.abs(width - lastWidth) > 40 || Math.abs(height - lastHeight) > 40) {
          setContainerSize({ width, height })
          lastWidth = width
          lastHeight = height
        }

        // Only update direction in TB/LR mode
        if (!isExpanded) {
          setGraphDirection(prev => (width >= 560 ? 'LR' : width < 520 ? 'TB' : prev))
        }
      }
      const resizeObserver = new ResizeObserver(() => {
        if (timeoutId) clearTimeout(timeoutId)
        timeoutId = setTimeout(update, 150)
      })
      resizeObserver.observe(containerEl)
      update()
      return () => {
        if (timeoutId) clearTimeout(timeoutId)
        resizeObserver.disconnect()
      }
    }, [containerEl, isExpanded])

    // Compute runMessage from latest doc_run in current messages
    const runMessage = useMemo(() => {
      const msgs = liveMessages && liveMessages.length > 0 ? liveMessages : currentMessages
      // The run the user clicked in the chat wins; otherwise show the latest run.
      const selected = selectedMessageId ? msgs.find(m => m.id === selectedMessageId) : undefined
      if (selected && selected.role === 'assistant' && selected.meta?.kind === 'doc_run') return selected
      for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i].role === 'assistant' && msgs[i].meta?.kind === 'doc_run') {
          return msgs[i]
        }
      }
      return null
    }, [currentMessages, liveMessages, selectedMessageId])

    // Fetch KG data when scope/conversation changes or run finishes (for Pipeline and Entities views)
    useEffect(() => {
      if (graphView !== 'pipeline' && graphView !== 'entities') {
        setKgData(null)
        return
      }

      const fetchData = async () => {
        try {
          const data = await fetchKnowledgeGraph(
            scope === 'all' ? 'all' : 'conversation',
            scope === 'current' ? currentConversationId : undefined,
          )
          setKgData(data)
        } catch {
          setKgData(null)
        }
      }

      fetchData()
    }, [graphView, scope, currentConversationId, runMessage?.id, runMessage?.isStreaming])

    // Build graph from appropriate source
    const graphData = useMemo(() => {
      // For Pipeline view: find latest doc_run and build pipeline
      if (graphView === 'pipeline') {
        if (!runMessage || !runMessage.meta?.tiles) {
          return null
        }

        // Find the preceding user message to extract pipeline question
        const msgs = liveMessages && liveMessages.length > 0 ? liveMessages : currentMessages
        let pipelineQuestion = '—'
        const runIdx = msgs.findIndex(m => m.id === runMessage.id)
        if (runIdx > 0) {
          for (let i = runIdx - 1; i >= 0; i--) {
            if (msgs[i].role === 'user') {
              pipelineQuestion = msgs[i].content || '—'
              break
            }
          }
        }

        // Build pipeline nodes/edges
        const tiles = (runMessage.meta?.tiles || []) as any[]
        const pipelineData = buildPipeline(
          {
            question: pipelineQuestion,
            tiles,
            answer: runMessage.content || '',
            intent: runMessage.meta?.intent as string,
          },
          kgData,
        )

        return pipelineData
      }

      // For Entities view: render KG entities with force layout
      if (graphView === 'entities') {
        if (!kgData || kgData.entities.length === 0) {
          return null
        }

        // Build KG nodes
        const entityNodes = kgData.entities.map((entity: any) => ({
          id: entity.id,
          kind: 'entity',
          label: entity.name,
          data: {
            type: entity.type,
            mention_count: entity.mention_count,
            ...entity.props,
          },
          conversationId: currentConversationId,
          runIds: [],
          messageIds: [],
        }))

        // Build KG edges
        const entityIds = new Set(entityNodes.map((n: any) => n.id))
        const entityEdges = kgData.relations
          .filter((r: any) => entityIds.has(r.src) && entityIds.has(r.dst))
          .map((rel: any) => ({
            id: rel.id,
            source: rel.src,
            target: rel.dst,
            label: rel.type.replace(/_/g, ' '),
            kind: rel.type,
            style:
              rel.type === 'candidate_profile' ? { strokeDasharray: '5 4', stroke: 'var(--text-muted)' } :
              rel.type === 'likely_profile' ? { stroke: 'var(--accent)', strokeWidth: 2 } :
              undefined,
          }))

        // Apply force layout
        const graph = { nodes: entityNodes, edges: entityEdges }
        const result = layoutForce(graph, {
          width: containerSize.width,
          height: containerSize.height,
          previous: previousPositionsRef.current.size > 0 ? previousPositionsRef.current : undefined,
          previousVirtualSize: previousVirtualSizeRef.current || undefined,
        })

        // Store positions and virtual size for next layout
        const newPrevious = new Map<string, { x: number; y: number }>()
        for (const node of result.nodes) {
          const size = NODE_SIZES[node.kind as keyof typeof NODE_SIZES] || NODE_SIZES.entity
          newPrevious.set(node.id, {
            x: node.position.x + size.width / 2,
            y: node.position.y + size.height / 2,
          })
        }
        previousPositionsRef.current = newPrevious
        previousVirtualSizeRef.current = { width: result.virtualWidth, height: result.virtualHeight }

        return result
      }

      // For Network/Layers: use existing logic
      const msgs = liveMessages && liveMessages.length > 0 ? liveMessages : currentMessages
      let convsToUse: { id: string; title: string; messages: Message[] }[] = []

      if (scope === 'current') {
        convsToUse = [{ id: currentConversationId, title: 'This conversation', messages: msgs }]
      } else {
        convsToUse = [...loadedConversations]
        // Include current if not already there
        if (!loadedConversations.find(c => c.id === currentConversationId)) {
          convsToUse.unshift({ id: currentConversationId, title: 'This conversation', messages: msgs })
        }
      }

      if (convsToUse.length === 0 || convsToUse[0].messages.length === 0) {
        return null
      }

      const graph = buildKnowledgeGraph(convsToUse, { highlightMessageId: selectedMessageId })

      // Use force layout if in network mode, otherwise use layered layout
      if (graphView === 'network') {
        // Prepare pinned coordinates (no scaling, just pass through)
        const scaledPinned = new Map<string, { x: number; y: number }>()
        if (pinnedRef.current.size > 0) {
          for (const [id, pos] of pinnedRef.current) {
            scaledPinned.set(id, { x: pos.x, y: pos.y })
          }
        }

        const result = layoutForce(graph, {
          width: containerSize.width,
          height: containerSize.height,
          previous: previousPositionsRef.current.size > 0 ? previousPositionsRef.current : undefined,
          pinned: scaledPinned.size > 0 ? scaledPinned : undefined,
          previousVirtualSize: previousVirtualSizeRef.current || undefined,
        })

        // Store positions and virtual size for next layout
        const newPrevious = new Map<string, { x: number; y: number }>()
        for (const node of result.nodes) {
          newPrevious.set(node.id, {
            x: node.position.x + NODE_SIZES[node.kind as keyof typeof NODE_SIZES].width / 2,
            y: node.position.y + NODE_SIZES[node.kind as keyof typeof NODE_SIZES].height / 2,
          })
        }
        previousPositionsRef.current = newPrevious
        previousVirtualSizeRef.current = { width: result.virtualWidth, height: result.virtualHeight }

        return result
      } else {
        const effectiveDirection = isExpanded ? 'LR' : graphDirection
        return layoutKnowledgeGraph(graph, { direction: effectiveDirection })
      }
    }, [graphView, scope, currentConversationId, currentMessages, liveMessages, loadedConversations, selectedMessageId, isExpanded, graphDirection, containerSize, kgData, runMessage?.id, runMessage?.isStreaming])

    // Track zoom level for LOD using a ref (useStore requires ReactFlowProvider context)
    const lodZoomRef = useRef(1)
    const handleMove = useCallback((_event: any, viewport: any) => {
      lodZoomRef.current = viewport.zoom
      const effectiveWidth = containerSize.width
      const effectiveSize = viewport.zoom * effectiveWidth
      setLodMode(effectiveSize < 300 ? 'compact' : 'full')
    }, [containerSize])

    // Calculate anyHighlighted for use in handlers
    const anyHighlighted = useMemo(() => {
      return graphData ? (graphData as any).nodes.some((n: any) => n.highlighted) : false
    }, [graphData])

    // Update React Flow
    useEffect(() => {
      if (!graphData) {
        setNodes([])
        setEdges([])
        return
      }

      const gd = graphData as any

      // Pipeline: buildPipeline already produced React Flow-ready nodes/edges
      // (type, size, zIndex, handles, markers, styles) — pass them through.
      // (Cast so TS doesn't narrow graphView for the shared code below.)
      if ((graphView as string) === 'pipeline') {
        setNodes(
          gd.nodes.map((n: any) => ({
            ...n,
            draggable: false,
            selected: n.type !== 'pipelineGroup' && selectedNode != null && selectedNode === n.data,
          })),
        )
        setEdges(
          gd.edges.map((e: any) => ({
            ...e,
            // Edge labels are SVG: text uses `fill`, the pill is the label background rect.
            labelStyle: { fontSize: 10, fill: 'var(--text-secondary)', fontFamily: 'var(--font-family-body)' },
            labelBgStyle: { fill: 'var(--bg-glass-strong)', stroke: 'var(--border-glass)', strokeWidth: 1 },
            labelBgPadding: [6, 3],
            labelBgBorderRadius: 999,
          })),
        )
        return
      }

      // Build neighbor set for hover dimming
      const hoveredNeighbors = new Set<string>()
      if (hoveredId && !anyHighlighted) {
        hoveredNeighbors.add(hoveredId)
        // Add direct neighbors via edges
        for (const edge of gd.edges) {
          if (edge.source === hoveredId) {
            hoveredNeighbors.add(edge.target)
          } else if (edge.target === hoveredId) {
            hoveredNeighbors.add(edge.source)
          }
        }
      }

      // LOD sizes: compact is smaller (14px label + 1 line ~28px height, 110px width)
      const compactSizes = {
        document: { width: 110, height: 28 },
        question: { width: 110, height: 28 },
        decision: { width: 110, height: 28 },
        model: { width: 110, height: 28 },
        planner: { width: 110, height: 28 },
        web: { width: 110, height: 28 },
        profile: { width: 110, height: 28 },
      }
      const isCompact = lodMode === 'compact'
      const sizes = isCompact ? compactSizes : NODE_SIZES

      const xyNodes: Node[] = gd.nodes
        .filter((n: any) => {
          // For pipeline/entities views, always include all nodes
          if (graphView === 'pipeline' || graphView === 'entities') {
            return true
          }
          // For network/layers, apply selectedKinds filter
          return selectedKinds.has(n.kind)
        })
        .map((n: any) => {
          const nodeSizes = sizes[n.kind as keyof typeof sizes] || sizes.document
          // For pipeline/entities, use kind directly; for network/layers, use compact variants when appropriate
          let nodeType: string
          if (graphView === 'pipeline' || graphView === 'entities') {
            nodeType = n.kind
          } else {
            nodeType = isCompact ? `${n.kind}Compact` : n.kind
          }

          const isDimmed = hoveredId && !anyHighlighted && !hoveredNeighbors.has(n.id)
          const lblVal = n.label ?? ''
          const lbl = typeof lblVal === 'string' ? lblVal : ''
          return {
            id: n.id,
            position: n.position,
            data: {
              label: lbl,
              ...n.data,
              kind: n.kind,
              highlighted: anyHighlighted ? n.highlighted : true,
              direction: isExpanded ? 'LR' : graphDirection,
              shortLabel: lbl.length > 18 ? lbl.substring(0, 18) + '…' : lbl,
              isCompact: isCompact && (graphView === 'network' || graphView === 'layers'),
              dimmed: isDimmed,
              isNetworkMode: graphView === 'network',
            },
            type: nodeType,
            style: {
              width: nodeSizes.width,
              height: nodeSizes.height,
              opacity: isDimmed ? 0.3 : 1,
              transition: isDimmed ? 'opacity 200ms ease-in-out' : 'opacity 200ms ease-in-out',
            },
            selected: selectedNode?.id === n.id,
            draggable: graphView === 'network' && n.kind !== 'pipeline',
          }
        })

      const sourceNodeIds = new Set(xyNodes.map(n => n.id))
      const containerWidth = containerRef.current?.clientWidth ?? 0
      const shouldShowEdgeLabels = !isCompact && (isExpanded || containerWidth >= 560)
      const xyEdges: Edge[] = gd.edges
        .filter((e: any) => sourceNodeIds.has(e.source) && sourceNodeIds.has(e.target))
        .map((e: any) => {
          // Truncate long labels to ~40 chars
          const lbl = e.label ?? ''
          const truncatedLabel = typeof lbl === 'string' && lbl.length > 40 ? lbl.substring(0, 37) + '…' : lbl

          // Show labels for hover neighbors' edges
          const isHoverEdge = hoveredId && !anyHighlighted && (
            (e.source === hoveredId || e.target === hoveredId)
          )
          const showLabel = shouldShowEdgeLabels && (e.highlighted || isHoverEdge)

          const isDimmedEdge = hoveredId && !anyHighlighted && !isHoverEdge

          // For pipeline/entities, preserve builder edge props; for network, use default styling
          let edgeType: string | undefined
          let edgeStyle: any = {
            opacity: anyHighlighted ? (e.highlighted ? 0.8 : 0.2) : isDimmedEdge ? 0.2 : 0.6,
            strokeDasharray: e.failed ? '5,5' : 'none',
            stroke: e.failed ? 'rgb(239, 68, 68)' : e.highlighted || isHoverEdge ? 'var(--accent)' : 'var(--border)',
            strokeWidth: (e.highlighted || isHoverEdge) ? 2.25 : 1.5,
          }
          let markerEnd: any = {
            type: MarkerType.ArrowClosed,
            width: 20,
            height: 20,
            color: e.highlighted || isHoverEdge ? 'var(--accent)' : 'var(--border)',
          }
          let animated = (e.highlighted || isHoverEdge) as boolean

          // For pipeline/entities, use builder edge props if present
          if (graphView === 'pipeline' || graphView === 'entities') {
            if ((e as any).style) {
              edgeStyle = (e as any).style
            }
            if ((e as any).animated) {
              animated = (e as any).animated
            }
            if ((e as any).type) {
              edgeType = (e as any).type
            }
            if ((e as any).markerEnd) {
              markerEnd = (e as any).markerEnd
            }
            if (graphView === 'entities') {
              edgeType = 'floating'
            }
          } else if (graphView === 'network') {
            edgeType = 'floating'
          }

          return {
            id: e.id,
            source: e.source,
            target: e.target,
            label: showLabel ? truncatedLabel : '',
            title: e.label, // tooltip with full text
            type: edgeType,
            markerEnd,
            style: edgeStyle,
            // Edge labels are SVG: text uses `fill`, the pill is the label background rect.
            labelStyle: { fontSize: 10, fill: 'var(--text-secondary)', fontFamily: 'var(--font-family-body)' },
            labelBgStyle: { fill: 'var(--bg-glass-strong)', stroke: 'var(--border-glass)', strokeWidth: 1 },
            labelBgPadding: [6, 3],
            labelBgBorderRadius: 999,
            animated,
          }
        })

      setNodes(xyNodes)
      setEdges(xyEdges)
    }, [graphData, selectedNode, selectedKinds, setNodes, setEdges, isExpanded, graphDirection, graphView])


    const handleNodeClick = (e: React.MouseEvent, node: any) => {
      e.stopPropagation()
      if (node.type === 'pipelineGroup') return // knowledge-store band is background only
      setSelectedNode(node.data)
    }

    const handleNodeDragStop = useCallback((_event: any, node: any) => {
      // Record the node's center position in pinned map
      if (graphView === 'network') {
        const kind = node.data.kind as keyof typeof NODE_SIZES
        const centerX = node.position.x + (NODE_SIZES[kind]?.width || 0) / 2
        const centerY = node.position.y + (NODE_SIZES[kind]?.height || 0) / 2
        pinnedRef.current.set(node.id, { x: centerX, y: centerY })
      }
    }, [graphView])

    const handleNodeMouseEnter = useCallback((e: React.MouseEvent, node: any) => {
      if (graphView === 'network' && !anyHighlighted) {
        setHoveredId(node.id)
      }
    }, [graphView, anyHighlighted])

    const handleNodeMouseLeave = useCallback(() => {
      setHoveredId(null)
    }, [])

    const handleUnpinAll = useCallback(() => {
      pinnedRef.current.clear()
      // Relayout by updating container size trigger
      setContainerSize(s => ({ ...s }))
    }, [])

    const toggleKindFilter = (kind: string) => {
      const newKinds = new Set(selectedKinds)
      if (newKinds.has(kind)) {
        newKinds.delete(kind)
      } else {
        newKinds.add(kind)
      }
      setSelectedKinds(newKinds)
    }

    // Reset pins and virtual size when scope changes
    useEffect(() => {
      pinnedRef.current.clear()
      previousPositionsRef.current = new Map()
      previousVirtualSizeRef.current = null
    }, [scope, currentConversationId])

    // Empty state — must stay AFTER every hook above (Rules of Hooks).
    if (!graphData || graphData.nodes.length === 0) {
      let emptyTitle = 'No runs yet'
      let emptyMessage = 'Ask something about your documents to build the graph.'

      if (graphView === 'entities') {
        emptyTitle = 'Knowledge store is empty'
        emptyMessage = 'Ask about a document (e.g. "who is this document about?") and Persephone will remember the people, roles and organisations it finds.'
      } else if (graphView === 'pipeline') {
        emptyTitle = 'No runs in this conversation'
        emptyMessage = 'Ask something in the Chat tab.'
      }

      return (
        <div className="w-full h-full flex flex-col items-center justify-center gap-4 text-[var(--text-muted)] p-8">
          <div className="relative w-32 h-32 opacity-30">
            <svg viewBox="0 0 100 100" className="w-full h-full">
              <circle cx="20" cy="20" r="8" fill="currentColor" />
              <circle cx="80" cy="80" r="8" fill="currentColor" />
              <circle cx="20" cy="80" r="8" fill="currentColor" />
              <line x1="20" y1="20" x2="80" y2="80" stroke="currentColor" strokeWidth="1" opacity="0.5" />
              <line x1="20" y1="20" x2="20" y2="80" stroke="currentColor" strokeWidth="1" opacity="0.5" />
            </svg>
          </div>
          <div className="text-center">
            <div className="font-display text-lg font-bold text-[var(--text-primary)] mb-1">{emptyTitle}</div>
            <div className="text-sm">{emptyMessage}</div>
          </div>
        </div>
      )
    }

    return (
      <div className="w-full h-full flex flex-col">
        {/* Header */}
        <div className="border-b border-[var(--glass-stroke)] bg-[var(--bg-glass-strong)] backdrop-blur px-3 py-2.5 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-3">
              {/* Scope selector */}
              <div className="flex items-center gap-2">
                <label className="text-[0.75rem] text-[var(--text-muted)] font-semibold uppercase tracking-wider">
                  Scope:
                </label>
                <select
                  value={scope}
                  onChange={e => setScope(e.target.value as 'current' | 'all')}
                  className="text-[0.75rem] px-2.5 py-1.5 rounded-lg glass-input text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]/30 transition-all font-medium"
                >
                  <option value="current">This conversation</option>
                  <option value="all">All ({conversations.length})</option>
                </select>
              </div>

              {/* View toggle: Pipeline, Network, Entities, Layers */}
              <div className="flex items-center gap-1 rounded-lg glass-card p-0.5">
                {(['pipeline', 'network', 'entities', 'layers'] as const).map(view => {
                  const titles: Record<string, string> = {
                    pipeline: 'Pipeline architecture',
                    network: 'Force-directed network layout',
                    entities: 'Entity graph',
                    layers: 'Layered hierarchical layout',
                  }
                  return (
                    <button
                      key={view}
                      onClick={() => setGraphView(view)}
                      className={clsx(
                        'px-2.5 py-1 rounded-md text-[0.7rem] font-semibold uppercase tracking-wider transition-all',
                        graphView === view
                          ? 'bg-[var(--accent)] text-white shadow-[0_0_8px_var(--accent-glow)]'
                          : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
                      )}
                      title={titles[view]}
                    >
                      {view}
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Expand button (only show when not already expanded, i.e., in panel view) */}
            {!isExpanded && onExpand && (
              <button
                onClick={onExpand}
                className="p-1.5 text-[var(--text-muted)] hover:text-[var(--accent)] rounded-md hover:bg-[var(--glass-fill-hover)] transition-colors"
                title="Expand to full screen"
              >
                <Maximize2 className="w-4 h-4" />
              </button>
            )}
          </div>

          {/* Filters and unpin button (hidden in Pipeline and Entities views) */}
          {graphView !== 'pipeline' && graphView !== 'entities' && (
          <div className="flex items-center gap-1 flex-wrap">
            <span className="text-[0.7rem] text-[var(--text-muted)] font-semibold uppercase tracking-wider">Show:</span>
            {(['document', 'question', 'decision', 'model'] as const).map(kind => {
              const labels: Record<string, string> = {
                document: isExpanded ? 'Document' : 'Docs',
                question: isExpanded ? 'Question' : 'Q',
                decision: isExpanded ? 'Decision' : 'Dec',
                model: isExpanded ? 'Model' : 'Mdl',
              }
              return (
                <button
                  key={kind}
                  onClick={() => toggleKindFilter(kind)}
                  className={clsx(
                    'px-2 py-0.5 rounded-lg text-[0.65rem] font-medium uppercase tracking-wider transition-all',
                    selectedKinds.has(kind)
                      ? 'bg-[var(--accent)] text-white shadow-[0_0_8px_var(--accent-glow)]'
                      : 'glass-card text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
                  )}
                >
                  {labels[kind]}
                </button>
              )
            })}
            {/* WEB chip for planner, web, profile */}
            <button
              onClick={() => {
                const newKinds = new Set(selectedKinds)
                const webKinds: Array<'planner' | 'web' | 'profile'> = ['planner', 'web', 'profile']
                const allActive = webKinds.every(k => newKinds.has(k))

                for (const k of webKinds) {
                  if (allActive) {
                    newKinds.delete(k)
                  } else {
                    newKinds.add(k)
                  }
                }
                setSelectedKinds(newKinds)
              }}
              className={clsx(
                'px-2 py-0.5 rounded-lg text-[0.65rem] font-medium uppercase tracking-wider transition-all',
                ['planner', 'web', 'profile'].every(k => selectedKinds.has(k))
                  ? 'bg-[var(--accent)] text-white shadow-[0_0_8px_var(--accent-glow)]'
                  : 'glass-card text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
              )}
            >
              Web
            </button>
            {pinnedRef.current.size > 0 && graphView === 'network' && (
              <button
                onClick={handleUnpinAll}
                className="flex items-center gap-1 px-2 py-0.5 rounded-lg text-[0.65rem] font-medium uppercase tracking-wider bg-[var(--accent)]/20 text-[var(--accent)] hover:bg-[var(--accent)]/30 transition-all"
                title="Clear all pinned nodes"
              >
                <Lock className="w-3 h-3" />
                Unpin all
              </button>
            )}
          </div>
          )}

          {scope === 'all' && loadingCount > 0 && (
            <div className="text-[0.7rem] text-[var(--text-muted)] font-medium">Loading {loadingCount} conversations…</div>
          )}
        </div>

        {/* Graph */}
        <div ref={setContainerNode} className={clsx('flex-1 relative', graphView === 'pipeline' && 'kg-pipeline')}>
          <ReactFlow
            key={graphView === 'pipeline' ? `pipeline-${runMessage?.id}` : graphView}
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={handleNodeClick}
            onNodeDragStop={handleNodeDragStop}
            onNodeMouseEnter={handleNodeMouseEnter}
            onNodeMouseLeave={handleNodeMouseLeave}
            onMove={handleMove}
            nodeTypes={nodeTypes}
            edgeTypes={graphView === 'network' ? edgeTypesNetwork : edgeTypesLayers}
            fitView
            fitViewOptions={graphView === 'pipeline' ? { padding: 0.08, minZoom: 0.2 } : { padding: 0.15, minZoom: 0.4 }}
            nodesConnectable={graphView !== 'pipeline'}
            minZoom={0.2}
            maxZoom={3}
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--text-muted)" style={{ opacity: 0.15 }} />

            <GraphResizeHandler
              containerRef={containerRef}
              padding={graphView === 'pipeline' ? 0.08 : 0.15}
              minZoom={graphView === 'pipeline' ? 0.2 : 0.4}
            />

            {graphView !== 'pipeline' && (
              <Panel position="bottom-left" className="pointer-events-none">
                <div className="pointer-events-auto">
                  <Legend />
                </div>
              </Panel>
            )}

            <div className="react-flow-controls-container">
              <Controls
                showZoom
                showFitView
                position="bottom-right"
                className="!border-[var(--border-glass)] !bg-[var(--bg-glass-strong)] !shadow-[var(--shadow-soft)] !w-8"
              />
            </div>
          </ReactFlow>

          {selectedNode && (
            <DetailCard node={selectedNode} onClose={() => setSelectedNode(null)} onSelectMessage={onSelectMessage} />
          )}
        </div>
      </div>
    )
  },
)

/**
 * Main KnowledgeGraph component
 */
export function KnowledgeGraph({
  conversations,
  currentConversationId,
  currentMessages,
  liveMessages,
  selectedMessageId,
  onSelectMessage,
}: KnowledgeGraphProps) {
  const [expandedModal, setExpandedModal] = useState(false)

  // For compact panel view
  return (
    <div className="w-full h-full flex flex-col">
      <KnowledgeGraphInner
        conversations={conversations}
        currentConversationId={currentConversationId}
        currentMessages={currentMessages}
        liveMessages={liveMessages}
        selectedMessageId={selectedMessageId}
        onSelectMessage={onSelectMessage}
        isExpanded={false}
        onExpand={() => setExpandedModal(true)}
      />

      {/* Expanded modal */}
      <GraphModal
        isOpen={expandedModal}
        conversations={conversations}
        currentConversationId={currentConversationId}
        currentMessages={currentMessages}
        liveMessages={liveMessages}
        selectedMessageId={selectedMessageId}
        onSelectMessage={onSelectMessage}
        onClose={() => setExpandedModal(false)}
      />
    </div>
  )
}
