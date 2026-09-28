/**
 * Knowledge graph → structured Network (ELK layered) and Layers (swimlanes).
 *
 * Both render the decluttered run graph from kgNetwork.ts. Network lays it out
 * with ELK (async, cached by graph signature, off the render path); Layers uses
 * the deterministic swimlane layout. Shared behaviour: focus mode (selected node
 * + neighbours, or the selected run), search, "collapse older runs", and a
 * right-side detail panel.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  Panel,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type Viewport,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Search, Maximize, X, ExternalLink, MessageSquare, ChevronsDownUp, ChevronsUpDown, ChevronDown, ChevronUp } from 'lucide-react'
import { clsx } from 'clsx'
import type { Message } from '@/types'
import type { Decision } from '@/lib/docAgent'
import {
  buildRunGraph,
  layoutLanes,
  runGraphSignature,
  runNodeSize,
  nodeSearchText,
  runNeighbourhood,
  findRunForMessage,
  formatRunTime,
  LANE_HEADER_HEIGHT,
  LANE_LABEL_WIDTH,
  type RunGraph,
  type RunNode,
  type RunInfo,
  type EdgeCategory,
  type DecisionChip,
} from './kgNetwork'
import { layoutRunGraphElk, type ElkLayoutResult, type Pt } from './kgLayout'
import { RunCardNode, RunBandNode, LaneBandNode, LaneHeaderNode, RUN_KIND_COLOR, RUN_KIND_LABEL } from './kgRunNodes'
import { RunGraphEdge } from './kgElkEdge'
import { flyToNodes, flyToOverview, restoreViewport, type ReplayFocus } from './kgReplayView'

const nodeTypes = { runCard: RunCardNode, runBand: RunBandNode, laneBand: LaneBandNode, laneHeader: LaneHeaderNode }
const edgeTypes = { runEdge: RunGraphEdge }

// ── ELK layout cache (module level: survives view switches / remounts) ──
const ELK_CACHE = new Map<string, ElkLayoutResult>()
const ELK_CACHE_MAX = 16
function cacheElk(sig: string, result: ElkLayoutResult) {
  ELK_CACHE.delete(sig)
  ELK_CACHE.set(sig, result)
  while (ELK_CACHE.size > ELK_CACHE_MAX) {
    const oldest = ELK_CACHE.keys().next().value
    if (oldest === undefined) break
    ELK_CACHE.delete(oldest)
  }
}

function usePersistedFlag(key: string, initial: boolean): [boolean, (v: boolean) => void] {
  const [value, setValue] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw === null ? initial : raw === '1'
    } catch {
      return initial
    }
  })
  const set = useCallback(
    (v: boolean) => {
      setValue(v)
      try {
        localStorage.setItem(key, v ? '1' : '0')
      } catch {
        // ignore
      }
    },
    [key],
  )
  return [value, set]
}

function truncate(s: string, n: number): string {
  const t = (s || '').replace(/\s+/g, ' ').trim()
  return t.length > n ? t.slice(0, n - 1) + '…' : t
}

function safeHttpUrl(url: unknown): string {
  return typeof url === 'string' && /^https?:\/\//i.test(url) ? url : ''
}

/** What gets drawn: graph nodes (or lane copies of them) with absolute rects. */
interface RenderNode {
  id: string
  refOf: string
  isRef: boolean
  laneKey: string | null
  x: number
  y: number
  width: number
  height: number
}
interface RenderEdge {
  id: string
  edgeId: string
  source: string
  target: string
  laneKey: string | null
  points: Pt[]
}

export interface KgRunViewProps {
  mode: 'network' | 'layers'
  convs: { id: string; title: string; messages: Message[] }[]
  selectedMessageId?: string | null
  onSelectMessage?: (assistantMessageId: string) => void
  isExpanded?: boolean
  /** Container width bucket — part of the layout cache key. */
  widthBucket: number
  /** Replay step to highlight (null = normal behaviour). */
  replay?: ReplayFocus | null
  /** The replay finished: show the whole graph (normal, interactive) until it is closed. */
  replayOverview?: boolean
}

export function KgRunView(props: KgRunViewProps) {
  return (
    <ReactFlowProvider>
      <KgRunViewInner {...props} />
    </ReactFlowProvider>
  )
}

function KgRunViewInner({ mode, convs, selectedMessageId, onSelectMessage, isExpanded = false, widthBucket, replay = null, replayOverview = false }: KgRunViewProps) {
  const rf = useReactFlow()
  const [collapseOlder, setCollapseOlder] = usePersistedFlag('persephone-docs-kg-net-collapse', false)
  // Legend: an explicit user choice is persisted; with no stored choice it
  // defaults to collapsed in narrow containers (< 640 px) and open otherwise.
  const [legendChoice, setLegendChoice] = useState<boolean | null>(() => {
    try {
      const raw = localStorage.getItem(LEGEND_KEY)
      return raw === null ? null : raw === '1'
    } catch {
      return null
    }
  })
  const [isNarrow, setIsNarrow] = useState(false)
  const legendOpen = legendChoice ?? !isNarrow
  const toggleLegend = useCallback(() => {
    const next = !legendOpen
    setLegendChoice(next)
    try {
      localStorage.setItem(LEGEND_KEY, next ? '1' : '0')
    } catch {
      // ignore
    }
  }, [legendOpen])
  const [expandedRuns, setExpandedRuns] = useState<Set<string>>(() => new Set())
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [activeRunKey, setActiveRunKey] = useState<string | null>(null)
  const [runFocusCleared, setRunFocusCleared] = useState(false)
  const [query, setQuery] = useState('')
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const wrapperRef = useRef<HTMLDivElement | null>(null)

  // A new selection from the chat re-enables run focus.
  useEffect(() => {
    setRunFocusCleared(false)
    setActiveRunKey(null)
  }, [selectedMessageId])
  useEffect(() => {
    setSelectedId(null)
  }, [mode])

  // The replayed run is always expanded (it may be an older, collapsed one).
  const forceExpandId = replay?.runKey || selectedMessageId
  const graph: RunGraph = useMemo(
    () => buildRunGraph(convs, { collapseOlder, expandedRuns, forceExpandMessageId: forceExpandId }),
    [convs, collapseOlder, expandedRuns, forceExpandId],
  )
  const graphNodeById = useMemo(() => new Map(graph.nodes.map(n => [n.id, n])), [graph])
  const graphEdgeById = useMemo(() => new Map(graph.edges.map(e => [e.id, e])), [graph])
  const runByKey = useMemo(() => new Map(graph.runs.map(r => [r.key, r])), [graph])

  // ── Layers: synchronous swimlanes ──
  const lanes = useMemo(() => (mode === 'layers' ? layoutLanes(graph) : null), [mode, graph])

  // ── Network: async ELK, cached by structure (not per streamed token) ──
  const elkSig = mode === 'network' ? `${runGraphSignature(graph)}#w${widthBucket}` : ''
  const [elk, setElk] = useState<{ sig: string; result: ElkLayoutResult } | null>(() => {
    const hit = elkSig ? ELK_CACHE.get(elkSig) : undefined
    return hit ? { sig: elkSig, result: hit } : null
  })
  const [elkError, setElkError] = useState<string | null>(null)
  const graphRef = useRef(graph)
  graphRef.current = graph
  useEffect(() => {
    if (mode !== 'network' || !elkSig) return
    const hit = ELK_CACHE.get(elkSig)
    if (hit) {
      setElk(prev => (prev?.sig === elkSig ? prev : { sig: elkSig, result: hit }))
      return
    }
    let cancelled = false
    layoutRunGraphElk(graphRef.current)
      .then(result => {
        cacheElk(elkSig, result)
        if (!cancelled) {
          setElk({ sig: elkSig, result })
          setElkError(null)
        }
      })
      .catch(err => {
        if (!cancelled) setElkError(err instanceof Error ? err.message : String(err))
      })
    return () => {
      cancelled = true
    }
  }, [mode, elkSig])

  // ── Render model (shared by both modes) ──
  const render = useMemo(() => {
    const rNodes: RenderNode[] = []
    const rEdges: RenderEdge[] = []
    if (mode === 'layers' && lanes) {
      for (const n of lanes.nodes) {
        rNodes.push({ id: n.id, refOf: n.refOf, isRef: n.isRef, laneKey: n.laneKey, x: n.x, y: n.y, width: n.width, height: n.height })
      }
      const byId = new Map(rNodes.map(n => [n.id, n]))
      for (const e of lanes.edges) {
        const s = byId.get(e.source)
        const t = byId.get(e.target)
        if (!s || !t) continue
        const a = { x: s.x + s.width, y: s.y + s.height / 2 }
        const b = { x: t.x, y: t.y + t.height / 2 }
        const midX = (a.x + b.x) / 2
        const points = Math.abs(a.y - b.y) < 0.5 ? [a, b] : [a, { x: midX, y: a.y }, { x: midX, y: b.y }, b]
        rEdges.push({ id: e.id, edgeId: e.edgeId, source: e.source, target: e.target, laneKey: e.laneKey, points })
      }
    } else if (mode === 'network' && elk) {
      const pos = elk.result.positions
      for (const n of graph.nodes) {
        const p = pos[n.id]
        if (!p) continue // new node, waiting for the next layout
        const size = runNodeSize(n)
        rNodes.push({ id: n.id, refOf: n.id, isRef: false, laneKey: n.runKey, x: p.x, y: p.y, width: size.width, height: size.height })
      }
      const present = new Set(rNodes.map(n => n.id))
      for (const e of graph.edges) {
        const pts = elk.result.routes[e.id]
        if (!pts || !present.has(e.source) || !present.has(e.target)) continue
        rEdges.push({ id: e.id, edgeId: e.id, source: e.source, target: e.target, laneKey: e.runKey, points: pts })
      }
    }
    return { nodes: rNodes, edges: rEdges, byId: new Map(rNodes.map(n => [n.id, n])) }
  }, [mode, lanes, elk, graph])

  // ── Selection / focus ──
  const selectedRender = selectedId && render.byId.has(selectedId) ? selectedId : null
  const selectedGraphNode: RunNode | null = selectedRender ? graphNodeById.get(render.byId.get(selectedRender)!.refOf) ?? null : null
  const selectedRenderState = selectedRender
  const selectedGraphNodeState = selectedGraphNode
  const selectedRun = useMemo(() => findRunForMessage(graph.runs, selectedMessageId), [graph.runs, selectedMessageId])
  const focusRunKey = activeRunKey && runByKey.has(activeRunKey) ? activeRunKey : !runFocusCleared ? selectedRun?.key ?? null : null
  const focusRunKeyState = focusRunKey
  const q = query.trim().toLowerCase()

  const matches = useMemo(() => {
    const set = new Set<string>()
    if (!q) return set
    for (const n of render.nodes) {
      const g = graphNodeById.get(n.refOf)
      if (g && nodeSearchText(g).includes(q)) set.add(n.id)
    }
    return set
  }, [q, render, graphNodeById])

  const focus = useMemo((): { mode: 'node' | 'search' | 'run'; nodes: Set<string>; edges: Set<string> } | null => {
    if (selectedRender) {
      const nodesSet = new Set<string>([selectedRender])
      const edgesSet = new Set<string>()
      for (const e of render.edges) {
        if (e.source === selectedRender || e.target === selectedRender) {
          edgesSet.add(e.id)
          nodesSet.add(e.source)
          nodesSet.add(e.target)
        }
      }
      // A shared document / model: light up every lane copy of it too.
      const refOf = render.byId.get(selectedRender)!.refOf
      for (const n of render.nodes) if (n.refOf === refOf) nodesSet.add(n.id)
      return { mode: 'node', nodes: nodesSet, edges: edgesSet }
    }
    if (q) return { mode: 'search', nodes: matches, edges: new Set() }
    if (focusRunKey) {
      const run = runByKey.get(focusRunKey)
      if (!run) return null
      if (mode === 'layers') {
        return {
          mode: 'run',
          nodes: new Set(render.nodes.filter(n => n.laneKey === focusRunKey).map(n => n.id)),
          edges: new Set(render.edges.filter(e => e.laneKey === focusRunKey).map(e => e.id)),
        }
      }
      const nb = runNeighbourhood(graph, run)
      return { mode: 'run', nodes: nb.nodes, edges: nb.edges }
    }
    return null
  }, [selectedRender, render, q, matches, focusRunKey, runByKey, mode, graph])
  const focusState = focus

  // ── Replay: graph ids → drawn ids (Layers repeats shared nodes per lane as `<id>@<runKey>`) ──
  const replayDrawn = useMemo(() => {
    if (!replay) return null
    const toDrawn = (id: string) => (render.byId.has(id) ? id : replay.runKey && render.byId.has(`${id}@${replay.runKey}`) ? `${id}@${replay.runKey}` : null)
    const nodes = replay.nodes.map(toDrawn).filter((x): x is string => !!x)
    const prevNodes = replay.prevNodes.map(toDrawn).filter((x): x is string => !!x)
    const edgeSet = new Set(replay.edges)
    const edges = new Set(render.edges.filter(e => edgeSet.has(e.edgeId) && (!replay.runKey || e.laneKey === replay.runKey || mode === 'network')).map(e => e.id))
    return { key: replay.key, nodes: new Set(nodes), primary: nodes, prevNodes, edges }
  }, [replay, render, mode])
  const replayRef = useRef(replay)
  replayRef.current = replay

  const selectRun = useCallback(
    (run: RunInfo) => {
      setSelectedId(null)
      setActiveRunKey(run.key)
      setRunFocusCleared(false)
      if (run.assistantMessageId && onSelectMessage) onSelectMessage(run.assistantMessageId)
    },
    [onSelectMessage],
  )

  // ── React Flow nodes / edges ──
  useEffect(() => {
    const out: Node[] = []
    const rp = replayDrawn
    // While a replay runs, its step replaces focus / selection / search highlighting.
    const focus = rp ? null : focusState
    const focusRunKey = rp ? replay?.runKey ?? null : focusRunKeyState
    const selectedRender = rp ? null : selectedRenderState
    const selectedGraphNode = rp ? null : selectedGraphNodeState
    const selectedRunKey = selectedGraphNode?.runKey ?? null
    if (mode === 'network' && elk) {
      for (const band of elk.result.bands) {
        const run = runByKey.get(band.runKey)
        if (!run) continue
        const active = focusRunKey === run.key || selectedRunKey === run.key
        const touched = !!focus && run.nodeIds.some(id => focus.nodes.has(id))
        out.push({
          id: `band:${run.key}`,
          type: 'runBand',
          position: { x: band.bbox.x, y: band.bbox.y },
          width: band.bbox.width,
          height: band.bbox.height,
          style: { width: band.bbox.width, height: band.bbox.height, pointerEvents: 'none' },
          zIndex: -1,
          selectable: false,
          draggable: false,
          focusable: false,
          data: {
            parts: band.parts.map(p => ({ x: p.rect.x - band.bbox.x, y: p.rect.y - band.bbox.y, width: p.rect.width, height: p.rect.height, role: p.role })),
            label: truncate(run.question, 220),
            time: formatRunTime(run.timestamp),
            status: run.status,
            active,
            dim: !!focus && !active && !touched,
            onSelect: () => selectRun(run),
          },
          ...(rp ? { className: active ? undefined : 'kg-rp-dim' } : {}),
        })
      }
    }
    if (mode === 'layers' && lanes) {
      out.push({
        id: 'lane-header',
        type: 'laneHeader',
        position: { x: 0, y: 0 },
        width: lanes.width,
        height: LANE_HEADER_HEIGHT,
        style: { width: lanes.width, height: LANE_HEADER_HEIGHT, pointerEvents: 'none' },
        zIndex: -1,
        selectable: false,
        draggable: false,
        focusable: false,
        data: { columns: lanes.columns.map(c => ({ x: c.x, width: c.width, title: c.title })), labelWidth: LANE_LABEL_WIDTH },
      })
      for (const lane of lanes.lanes) {
        const active = focusRunKey === lane.key || selectedRunKey === lane.key
        const touched = !!focus && render.nodes.some(n => n.laneKey === lane.key && focus.nodes.has(n.id))
        out.push({
          id: `lane:${lane.key}`,
          type: 'laneBand',
          position: { x: 0, y: lane.y },
          width: lanes.width,
          height: lane.height,
          style: { width: lanes.width, height: lane.height, pointerEvents: 'none' },
          zIndex: -1,
          selectable: false,
          draggable: false,
          focusable: false,
          data: {
            label: truncate(lane.run.question, 140),
            time: formatRunTime(lane.run.timestamp),
            status: lane.run.status,
            index: lane.run.index,
            collapsed: lane.run.collapsed,
            labelWidth: LANE_LABEL_WIDTH,
            active,
            dim: !!focus && !active && !touched,
            onSelect: () => selectRun(lane.run),
          },
          ...(rp ? { className: active ? undefined : 'kg-rp-dim' } : {}),
        })
      }
    }
    for (const rn of render.nodes) {
      const g = graphNodeById.get(rn.refOf)
      if (!g) continue
      const dim = !!focus && !focus.nodes.has(rn.id)
      const rpActive = !!rp && rp.nodes.has(rn.id)
      const rpContext = !!rp && !rpActive && rp.prevNodes.includes(rn.id)
      out.push({
        id: rn.id,
        type: 'runCard',
        position: { x: rn.x, y: rn.y },
        width: rn.width,
        height: rn.height,
        style: { width: rn.width, height: rn.height, opacity: dim ? 0.25 : 1, transition: 'opacity 200ms ease' },
        draggable: false,
        ...(rp ? { className: rpActive ? 'kg-rp-active' : rpContext ? 'kg-rp-context' : 'kg-rp-dim' } : {}),
        data: {
          kind: g.kind,
          label: g.label,
          data: g.data,
          variant: mode === 'layers' ? 'lanes' : 'network',
          isRef: rn.isRef,
          selected: rp ? rpActive : rn.id === selectedRender,
          focused: !!focus && focus.mode !== 'search' && focus.nodes.has(rn.id),
          match: !!focus && focus.mode === 'search' && matches.has(rn.id),
        },
      })
    }
    setNodes(out)

    setEdges(
      render.edges.map(re => {
        const ge = graphEdgeById.get(re.edgeId)
        const inFocus = !!focus && focus.edges.has(re.id)
        const dim = !!focus && (focus.mode === 'search' ? !(matches.has(re.source) && matches.has(re.target)) : !inFocus)
        const rpFlow = !!rp && rp.edges.has(re.id)
        return {
          id: re.id,
          source: re.source,
          target: re.target,
          type: 'runEdge',
          selectable: false,
          focusable: false,
          ...(rp ? { className: rpFlow ? 'kg-rp-flow' : 'kg-rp-dim', zIndex: rpFlow ? 4 : 0 } : {}),
          data: {
            points: re.points,
            category: (ge?.category || 'other') as EdgeCategory,
            label: ge?.label || '',
            title: ge?.title,
            active: rp ? rpFlow : inFocus && focus?.mode !== 'search',
            dim: rp ? !rpFlow : dim,
            failed: !!ge?.failed,
            showLabel: rp ? rpFlow : inFocus && focus?.mode === 'node',
          },
        }
      }),
    )
  }, [mode, elk, lanes, render, graphNodeById, graphEdgeById, runByKey, focusState, focusRunKeyState, selectedRenderState, selectedGraphNodeState, matches, selectRun, setNodes, setEdges, replayDrawn, replay?.runKey])

  // ── Viewport ──
  /** Fit everything ("Fit" button, explicit). */
  const fitAll = useCallback(() => {
    rf.fitView({ padding: 0.08, duration: 300, minZoom: 0.1, maxZoom: 1.3 })
  }, [rf])
  /**
   * Auto-fit (open / scope / tab switch / resize): fit the whole graph when it
   * stays readable (zoom ≥ READABLE_ZOOM, i.e. ≥ ~11 px text); otherwise fit the
   * selected — or latest — run at a readable zoom and let the user pan.
   */
  const focusIdsRef = useRef<string[]>([])
  {
    const run = selectedRun ?? graph.runs[graph.runs.length - 1] ?? null
    // A single run is the whole graph (its documents sit outside the run band) — fit all of it.
    focusIdsRef.current = !run || graph.runs.length <= 1
      ? []
      : mode === 'layers'
        ? [`lane:${run.key}`, ...render.nodes.filter(n => n.laneKey === run.key).map(n => n.id)]
        : [`band:${run.key}`, ...run.nodeIds.filter(id => render.byId.has(id))]
  }
  const fitReadable = useCallback(() => {
    if (replayRef.current) return // the replay drives the camera
    const el = wrapperRef.current?.querySelector('.react-flow') as HTMLElement | null
    const all = rf.getNodes()
    if (!el || all.length === 0) return
    const b = rf.getNodesBounds(all)
    const w = el.clientWidth
    const h = el.clientHeight
    if (!b.width || !b.height || !w || !h) return
    const zoomAll = Math.min(w / (b.width * 1.06), h / (b.height * 1.06))
    if (zoomAll >= FIT_ALL_MIN_ZOOM) {
      rf.fitView({ padding: 0.06, duration: 300, maxZoom: 1.3 })
      return
    }
    const present = new Set(all.map(n => n.id))
    const ids = focusIdsRef.current.filter(id => present.has(id)).map(id => ({ id }))
    if (ids.length === 0) {
      rf.fitView({ padding: 0.06, duration: 300, minZoom: Math.min(READABLE_ZOOM, Math.max(MIN_RUN_ZOOM, zoomAll)), maxZoom: 1.3 })
      return
    }
    // A run that is itself too wide for READABLE_ZOOM (e.g. document → question
    // → Laya → planner → web → profile in a docked panel) is fitted whole (down
    // to MIN_RUN_ZOOM) instead of being clipped on both sides.
    const idSet = new Set(ids.map(n => n.id))
    const rb = rf.getNodesBounds(all.filter(n => idSet.has(n.id)))
    const zoomRun = rb.width && rb.height ? Math.min(w / (rb.width * 1.08), h / (rb.height * 1.08)) : READABLE_ZOOM
    const floor = Math.min(READABLE_ZOOM, Math.max(MIN_RUN_ZOOM, zoomRun))
    rf.fitView({ nodes: ids, padding: 0.08, duration: 300, minZoom: floor, maxZoom: 1.3 })
  }, [rf])
  // Fit when the view / number of runs / collapse state changes (not per tile).
  const fitKey = `${mode}|${graph.runs.length}|${collapseOlder ? 1 : 0}|${render.nodes.length > 0 ? 1 : 0}`
  const lastFitKey = useRef('')
  useEffect(() => {
    if (render.nodes.length === 0 || lastFitKey.current === fitKey) return
    lastFitKey.current = fitKey
    const t = setTimeout(fitReadable, 60)
    return () => clearTimeout(t)
  }, [fitKey, render.nodes.length, fitReadable])
  // Chat selection changed → bring that run into view.
  const lastSelectedMsg = useRef<string | null | undefined>(selectedMessageId)
  useEffect(() => {
    if (replayRef.current) return
    if (lastSelectedMsg.current === selectedMessageId || !selectedRun || render.nodes.length === 0) return
    lastSelectedMsg.current = selectedMessageId
    const ids =
      mode === 'layers'
        ? render.nodes.filter(n => n.laneKey === selectedRun.key).map(n => ({ id: n.id }))
        : selectedRun.nodeIds.filter(id => render.byId.has(id)).map(id => ({ id }))
    if (ids.length === 0) return
    const t = setTimeout(() => rf.fitView({ nodes: ids, padding: 0.35, duration: 400, maxZoom: 1.1 }), 60)
    return () => clearTimeout(t)
  }, [selectedMessageId, selectedRun, render, mode, rf])
  // Track container width for the legend's default (collapsed when narrow).
  useEffect(() => {
    const el = wrapperRef.current
    if (!el) return
    const measure = () => setIsNarrow(el.clientWidth < LEGEND_NARROW_PX)
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  // Refit on container resize (debounced).
  useEffect(() => {
    const el = wrapperRef.current
    if (!el) return
    let t: ReturnType<typeof setTimeout> | undefined
    const ro = new ResizeObserver(() => {
      if (t) clearTimeout(t)
      t = setTimeout(fitReadable, 150)
    })
    ro.observe(el)
    return () => {
      if (t) clearTimeout(t)
      ro.disconnect()
    }
  }, [fitReadable])

  // ── Replay camera: save the viewport when a replay starts, fly to each step,
  // zoom out to the whole graph when it finishes, and on stop restore the exact
  // viewport — unless it is closed from the finished overview (that view stays). ──
  const savedViewportRef = useRef<Viewport | null>(null)
  const overviewRef = useRef(false)
  const replaySession = !!replay || replayOverview
  useEffect(() => {
    if (replaySession) {
      if (!savedViewportRef.current) savedViewportRef.current = rf.getViewport()
      overviewRef.current = replayOverview
      if (!replayOverview) return
      const t = setTimeout(() => flyToOverview(rf, 0.08), 40)
      return () => clearTimeout(t)
    }
    const vp = savedViewportRef.current
    const keep = overviewRef.current
    savedViewportRef.current = null
    overviewRef.current = false
    if (vp && !keep) restoreViewport(rf, vp)
  }, [replaySession, replayOverview, rf])
  useEffect(() => {
    if (!replayDrawn) return
    const t = setTimeout(() => {
      const el = wrapperRef.current?.querySelector('.react-flow') as HTMLElement | null
      if (!el) return
      flyToNodes(rf, replayDrawn.primary, replayDrawn.prevNodes, { width: el.clientWidth, height: el.clientHeight }, { minZoom: 0.5, maxZoom: 1.15 })
    }, 40)
    return () => clearTimeout(t)
    // Re-fly when the step changes or its nodes first appear (async ELK layout).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replayDrawn?.key, replayDrawn?.primary.length, rf])

  const focusFirstMatch = useCallback(() => {
    const first = render.nodes.find(n => matches.has(n.id))
    if (first) rf.fitView({ nodes: [{ id: first.id }], padding: 0.6, duration: 400, maxZoom: 1.2 })
  }, [render.nodes, matches, rf])

  // ── Handlers ──
  const handleNodeClick = useCallback(
    (e: React.MouseEvent, node: Node) => {
      e.stopPropagation()
      if (node.type !== 'runCard') return
      const rn = render.byId.get(node.id)
      const g = rn ? graphNodeById.get(rn.refOf) : undefined
      if (!rn || !g) return
      setActiveRunKey(null)
      if (g.kind === 'runCompact' && g.runKey) {
        const key = g.runKey
        setExpandedRuns(prev => new Set(prev).add(key))
        setSelectedId(`q:${key}`)
        return
      }
      setSelectedId(prev => (prev === node.id ? null : node.id))
    },
    [render, graphNodeById],
  )
  const handlePaneClick = useCallback(() => {
    setSelectedId(null)
    setActiveRunKey(null)
    setRunFocusCleared(true)
  }, [])

  const detailRun = selectedGraphNode?.runKey ? runByKey.get(selectedGraphNode.runKey) ?? null : null
  const panelOpen = !!selectedGraphNode
  const runsCount = graph.runs.length
  const collapsedCount = graph.runs.filter(r => r.collapsed).length
  const waitingForLayout = mode === 'network' && !elk && !elkError

  return (
    <div ref={wrapperRef} className="kg-network w-full h-full flex flex-col">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-1.5 min-w-0 px-3 py-1.5 border-b border-[var(--glass-stroke)] bg-[var(--bg-glass-strong)]/60">
        <div className={clsx('relative min-w-0 flex-1', isExpanded ? 'max-w-[14rem]' : 'max-w-[12rem]')}>
          <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-[var(--text-muted)] pointer-events-none" />
          <input
            type="search"
            value={query}
            onChange={e => {
              setQuery(e.target.value)
              setSelectedId(null)
            }}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                e.preventDefault()
                focusFirstMatch()
              } else if (e.key === 'Escape') {
                setQuery('')
              }
            }}
            placeholder="Search graph…"
            aria-label="Search the graph"
            className={clsx(
              'text-[0.72rem] pl-6 pr-2 py-1 rounded-lg glass-input text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)] focus:ring-1 focus:ring-[var(--accent)]/30 transition-all',
              'w-full min-w-[6rem]',
            )}
          />
        </div>
        {q && (
          <span className="text-[0.68rem] text-[var(--text-muted)] font-mono">
            {matches.size} match{matches.size === 1 ? '' : 'es'}
          </span>
        )}
        <button type="button" onClick={fitAll} className="pill-btn-outline text-[0.7rem] !py-1 !px-2 inline-flex items-center gap-1" title="Fit the whole graph">
          <Maximize className="w-3 h-3" />
          Fit
        </button>
        <button
          type="button"
          onClick={() => {
            setCollapseOlder(!collapseOlder)
            setExpandedRuns(new Set())
          }}
          aria-pressed={collapseOlder}
          className={clsx(
            'px-2 py-1 rounded-lg text-[0.68rem] font-semibold transition-all inline-flex items-center gap-1',
            collapseOlder
              ? 'bg-[var(--accent)] text-white shadow-[0_0_8px_var(--accent-glow)]'
              : 'glass-card text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
          )}
          title="Show only the latest 3 runs in full; older runs become compact cards (click one to expand it)"
        >
          {collapseOlder ? <ChevronsDownUp className="w-3 h-3" /> : <ChevronsUpDown className="w-3 h-3" />}
          Collapse older runs
        </button>
        <span className="ml-auto text-[0.68rem] text-[var(--text-muted)] whitespace-nowrap">
          {runsCount} run{runsCount === 1 ? '' : 's'}
          {collapsedCount > 0 ? ` · ${collapsedCount} collapsed` : ''}
        </span>
      </div>

      <div className="flex-1 relative min-h-0">
        {runsCount === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-sm text-[var(--text-muted)] p-8 text-center">
            No runs yet — ask something about your documents to build the graph.
          </div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={handleNodeClick}
            onPaneClick={handlePaneClick}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable={false}
            minZoom={0.1}
            maxZoom={2.5}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--text-muted)" style={{ opacity: 0.12 }} />
            <Panel position="bottom-left" className="pointer-events-none !m-2 max-w-[calc(100%-80px)]">
              <RunLegend mode={mode} open={legendOpen} onToggle={toggleLegend} />
            </Panel>
            <Controls
              showZoom
              showFitView={false}
              showInteractive={false}
              position="bottom-right"
              className={clsx('!border-[var(--border-glass)] !bg-[var(--bg-glass-strong)] !shadow-[var(--shadow-soft)]', panelOpen && !replay && 'kg-controls-shifted')}
            />
          </ReactFlow>
        )}

        {waitingForLayout && runsCount > 0 && (
          <div className="absolute top-2 left-1/2 -translate-x-1/2 text-[0.7rem] text-[var(--text-muted)] glass-card rounded-full px-3 py-1 animate-pulse">
            Laying out graph…
          </div>
        )}
        {elkError && (
          <div role="alert" className="absolute top-2 left-2 right-2 text-[0.72rem] text-red-500 glass-card rounded-xl px-3 py-2">
            Structured layout failed ({elkError}). Switch to the Organic layout to keep exploring.
          </div>
        )}

        {selectedGraphNode && !replay && (
          <RunDetailPanel
            node={selectedGraphNode}
            run={detailRun}
            onClose={() => setSelectedId(null)}
            onShowInChat={onSelectMessage}
            onExpandRun={key => {
              setExpandedRuns(prev => new Set(prev).add(key))
              setSelectedId(`q:${key}`)
            }}
          />
        )}
      </div>
    </div>
  )
}

// ── Legend ──────────────────────────────────────────────────────────────────

const LEGEND_EDGES: { category: EdgeCategory; label: string }[] = [
  { category: 'document', label: 'document' },
  { category: 'decision', label: 'decision' },
  { category: 'model', label: 'model' },
  { category: 'web', label: 'web' },
  { category: 'store', label: 'store' },
  { category: 'signature', label: 'signature' },
]

const LEGEND_KEY = 'persephone-docs-kg-legend-open'
/** Below this zoom card text drops under ~11 px — auto-fit focuses one run instead. */
const READABLE_ZOOM = 0.8
/** Fit the whole graph as long as it stays at least this legible (a single run always fits a docked panel). */
const FIT_ALL_MIN_ZOOM = 0.64
/** Lowest auto-fit zoom for a single run that doesn't fit at READABLE_ZOOM. */
const MIN_RUN_ZOOM = 0.45
const LEGEND_NARROW_PX = 640

function RunLegend({ mode, open, onToggle }: { mode: 'network' | 'layers'; open: boolean; onToggle: () => void }) {
  const kinds = ['document', 'question', 'decisions', 'model', 'web', 'store', 'signature'] as const
  const pillClass =
    'pointer-events-auto inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)] hover:text-[var(--text-secondary)] bg-[var(--bg-glass-strong)] border border-[var(--border-glass)] backdrop-blur transition-colors'
  if (!open) {
    return (
      <button type="button" onClick={onToggle} className={pillClass} aria-expanded={false} title="Show legend">
        Legend
        <ChevronUp className="w-3 h-3" />
      </button>
    )
  }
  return (
    <div className="kg-legend pointer-events-auto flex flex-wrap items-center gap-x-2.5 gap-y-1 max-w-[34rem] rounded-xl px-2.5 py-1.5 text-[10px] text-[var(--text-muted)] bg-[var(--bg-glass-strong)] border border-[var(--border-glass)] backdrop-blur">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded
        title="Hide legend"
        className="inline-flex items-center gap-0.5 font-semibold uppercase tracking-wider hover:text-[var(--text-secondary)] transition-colors"
      >
        Legend
        <ChevronDown className="w-3 h-3" />
      </button>
      <span className="w-px h-3 bg-[var(--glass-stroke)]" />
      {kinds.map(k => (
        <span key={k} className="inline-flex items-center gap-1">
          <span className="w-2 h-2 rounded-[3px]" style={{ background: RUN_KIND_COLOR[k] }} />
          {k === 'decisions' ? 'Laya' : k === 'store' ? 'Knowledge' : RUN_KIND_LABEL[k]}
        </span>
      ))}
      <span className="w-px h-3 bg-[var(--glass-stroke)]" />
      {LEGEND_EDGES.map(e => (
        <span key={e.category} className="inline-flex items-center gap-1">
          <span className={clsx('kg-legend__line', `kg-legend__line--${e.category}`)} />
          {e.label}
        </span>
      ))}
      {mode === 'layers' && (
        <>
          <span className="w-px h-3 bg-[var(--glass-stroke)]" />
          <span className="inline-flex items-center gap-1">
            <span className="w-3 h-2 rounded-[3px] border border-dashed border-[var(--text-muted)]" />
            shared (reference)
          </span>
        </>
      )}
    </div>
  )
}

// ── Detail panel ────────────────────────────────────────────────────────────

const LABEL_CLS = 'text-[0.62rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-1'
const VALUE_CLS = 'text-[0.76rem] text-[var(--text-secondary)] leading-snug'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className={LABEL_CLS}>{title}</div>
      {children}
    </div>
  )
}

function Chips({ items, color = 'var(--accent)' }: { items: string[]; color?: string }) {
  if (items.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1">
      {items.map(t => (
        <span
          key={t}
          className="px-2 py-0.5 rounded-full text-[0.66rem] font-semibold"
          style={{ color, background: `color-mix(in oklab, ${color} 14%, transparent)` }}
        >
          {t}
        </span>
      ))}
    </div>
  )
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : typeof v === 'number' ? String(v) : ''
}

function RunDetailPanel({
  node,
  run,
  onClose,
  onShowInChat,
  onExpandRun,
}: {
  node: RunNode
  run: RunInfo | null
  onClose: () => void
  onShowInChat?: (assistantMessageId: string) => void
  onExpandRun: (runKey: string) => void
}) {
  const d = (node?.data || {}) as Record<string, unknown>
  const kind = node?.kind
  const color = kind ? RUN_KIND_COLOR[kind] : 'var(--accent)'
  const answer = (d.answer || null) as { model?: string | null; ms?: number | null; status?: string; error?: string | null; preview?: string } | null
  const profileUrl = kind === 'profile' ? safeHttpUrl(d.url) : ''

  return (
    <aside className="absolute right-2 top-2 bottom-2 w-72 z-10 animate-in slide-in-from-right-4 duration-200" aria-label="Node details">
      {/* .glass-strong sets its own `position`, so the positioning lives on this wrapper. */}
      <div className="glass-strong kg-side-panel w-full h-full rounded-2xl flex flex-col overflow-hidden shadow-[var(--shadow-soft)]">
      <div className="flex items-start gap-2 px-3.5 pt-3 pb-2 border-b border-[var(--glass-stroke)]">
        <div className="min-w-0 flex-1">
          <div className="text-[0.6rem] font-bold uppercase tracking-[0.12em]" style={{ color }}>
            {kind ? RUN_KIND_LABEL[kind] : 'Node'}
          </div>
          <div className="text-[0.85rem] font-semibold text-[var(--text-primary)] leading-snug break-words line-clamp-3" title={str(d.fullText) || str(d.fullLabel) || node?.label}>
            {node?.label || '—'}
          </div>
          {run && (
            <div className="text-[0.66rem] text-[var(--text-muted)] mt-0.5 font-mono">
              Run {run.index + 1}
              {run.timestamp ? ` · ${formatRunTime(run.timestamp)}` : ''}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="flex-shrink-0 text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--glass-fill-hover)] rounded p-1 transition-colors"
          aria-label="Close details"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3.5 py-3 space-y-3">
        {(kind === 'question' || kind === 'runCompact') && (
          <>
            <Section title="Question">
              <div className={clsx(VALUE_CLS, 'whitespace-pre-wrap break-words')}>{str(d.fullText) || node.label}</div>
            </Section>
            {kind === 'runCompact' && Array.isArray(d.chips) && (d.chips as DecisionChip[]).length > 0 && (
              <Section title="Laya decisions">
                <Chips items={(d.chips as DecisionChip[]).map(c => c.text)} color="var(--gold)" />
              </Section>
            )}
            {answer ? (
              <Section title="Answer">
                {answer.preview ? <div className={clsx(VALUE_CLS, 'whitespace-pre-wrap break-words line-clamp-[12]')}>{answer.preview}</div> : null}
                <div className="text-[0.66rem] text-[var(--text-muted)] mt-1.5 font-mono">
                  {answer.model || '—'}
                  {typeof answer.ms === 'number' ? ` · ${(answer.ms / 1000).toFixed(1)}s` : ''}
                  {answer.status && answer.status !== 'done' ? ` · ${answer.status}` : ''}
                </div>
                {answer.error ? <div className="text-[0.7rem] text-red-500 mt-1">{answer.error}</div> : null}
              </Section>
            ) : (
              <div className="text-[0.72rem] text-[var(--text-muted)]">No answer yet.</div>
            )}
            {kind === 'runCompact' && node.runKey && (
              <button type="button" onClick={() => onExpandRun(node.runKey!)} className="pill-btn-outline text-[0.7rem] inline-flex items-center gap-1">
                <ChevronsUpDown className="w-3 h-3" />
                Expand this run
              </button>
            )}
          </>
        )}

        {kind === 'decisions' && <DecisionsDetail decisions={(Array.isArray(d.decisions) ? d.decisions : []) as Decision[]} />}

        {kind === 'model' && (
          <>
            <Section title="Model">
              <div className={clsx(VALUE_CLS, 'font-mono break-all')}>{str(d.modelName) || node.label}</div>
              {d.isLaya ? <div className="text-[0.7rem] text-[var(--text-muted)] mt-1">Non-generative decision model — classifies intent and routes each run.</div> : null}
            </Section>
            <Section title="Roles">
              <Chips items={Array.isArray(d.roles) ? (d.roles as string[]) : []} />
            </Section>
            <Section title="Used in">
              <div className={VALUE_CLS}>
                {Number(d.useCount) || 0} run{Number(d.useCount) === 1 ? '' : 's'}
              </div>
            </Section>
          </>
        )}

        {kind === 'document' && (
          <>
            <Section title="File name">
              <div className={clsx(VALUE_CLS, 'break-all font-mono text-[0.7rem]')}>{str(d.filename) || node.label}</div>
            </Section>
            <div className="grid grid-cols-2 gap-2">
              <Section title="Kind">
                <div className={clsx(VALUE_CLS, 'capitalize')}>{str(d.kind) || '—'}</div>
              </Section>
              <Section title="Pages">
                <div className={VALUE_CLS}>{str(d.pages) || '—'}</div>
              </Section>
            </div>
            {Array.isArray(d.roles) && (d.roles as string[]).length > 0 && (
              <Section title="Roles">
                <Chips items={d.roles as string[]} />
              </Section>
            )}
            <Section title="Used in">
              <div className={VALUE_CLS}>
                {Array.isArray(d.runKeys) ? (d.runKeys as string[]).length : 0} run(s)
              </div>
            </Section>
            <Section title="Document ID">
              <div className="text-[0.66rem] text-[var(--text-muted)] font-mono break-all">{str(d.docId)}</div>
            </Section>
          </>
        )}

        {kind === 'planner' && (
          <>
            {str(d.detail) && (
              <Section title="Plan">
                <div className={clsx(VALUE_CLS, 'break-words')}>{str(d.detail)}</div>
              </Section>
            )}
            <ItemList title="Queries" items={Array.isArray(d.items) ? (d.items as { kind?: string; label?: string }[]).filter(i => i?.kind === 'query').map(i => str(i.label)) : []} mono />
          </>
        )}

        {kind === 'web' && (
          <>
            {str(d.detail) && (
              <Section title="Summary">
                <div className={clsx(VALUE_CLS, 'break-words')}>{str(d.detail)}</div>
              </Section>
            )}
            {str(d.model) && (
              <Section title="Tool model">
                <div className={clsx(VALUE_CLS, 'font-mono break-all')}>{str(d.model)}</div>
              </Section>
            )}
            <ItemList title="Queries" items={Array.isArray(d.queries) ? (d.queries as string[]) : []} mono />
            {Array.isArray(d.results) && (d.results as { label: string; url: string | null }[]).length > 0 && (
              <Section title="Results">
                <div className="space-y-1">
                  {(d.results as { label: string; url: string | null }[]).slice(0, 12).map((r, i) => {
                    const url = safeHttpUrl(r.url)
                    return url ? (
                      <a
                        key={i}
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-start gap-1 text-[0.72rem] text-[var(--accent)] hover:text-[var(--accent-hover)] break-all"
                      >
                        <ExternalLink className="w-3 h-3 flex-shrink-0 mt-0.5" />
                        {r.label || url}
                      </a>
                    ) : (
                      <div key={i} className={clsx(VALUE_CLS, 'text-[0.72rem] break-words')}>
                        {r.label}
                      </div>
                    )
                  })}
                </div>
              </Section>
            )}
          </>
        )}

        {kind === 'profile' && (
          <>
            <Section title="Profile">
              <div className={clsx(VALUE_CLS, 'break-words')}>{str(d.fullLabel) || node.label}</div>
            </Section>
            <Section title="Host">
              <div className={VALUE_CLS}>{str(d.host) || '—'}</div>
            </Section>
            {str(d.url) && (
              <Section title="URL">
                {profileUrl ? (
                  <a href={profileUrl} target="_blank" rel="noopener noreferrer" className="text-[0.72rem] text-[var(--accent)] hover:text-[var(--accent-hover)] break-all">
                    {profileUrl}
                  </a>
                ) : (
                  <div className={clsx(VALUE_CLS, 'break-all')}>{str(d.url)}</div>
                )}
              </Section>
            )}
            {str(d.detail) && (
              <Section title="Snippet">
                <div className={clsx(VALUE_CLS, 'break-words')}>{str(d.detail)}</div>
              </Section>
            )}
          </>
        )}

        {kind === 'signature' && (
          <>
            <Section title="Score">
              <div className={clsx(VALUE_CLS, 'break-words')}>{str(d.detail) || '—'}</div>
            </Section>
            {(str(d.questioned) || (Array.isArray(d.references) && d.references.length > 0)) && (
              <Section title="Questioned vs references">
                <div className="grid grid-cols-3 gap-1">
                  {str(d.questioned) && (
                    <a href={str(d.questioned)} target="_blank" rel="noopener noreferrer" className="col-span-3 block rounded border border-[var(--glass-stroke)] bg-white overflow-hidden">
                      <img src={str(d.questioned)} alt="Questioned signature" className="w-full h-14 object-contain" />
                    </a>
                  )}
                  {(Array.isArray(d.references) ? (d.references as string[]) : []).slice(0, 6).map((u, i) => (
                    <a key={u} href={u} target="_blank" rel="noopener noreferrer" className="block rounded border border-[var(--glass-stroke)] bg-white overflow-hidden" title={`R${i + 1}`}>
                      <img src={u} alt={`Reference ${i + 1}`} className="w-full h-8 object-contain" />
                    </a>
                  ))}
                </div>
              </Section>
            )}
            {str(d.model) && (
              <Section title="Signature model">
                <div className={clsx(VALUE_CLS, 'font-mono')}>{str(d.model)}</div>
              </Section>
            )}
            <ItemList title="Reasons" items={Array.isArray(d.reasons) ? (d.reasons as string[]) : []} />
            <ItemList title="Warnings" items={Array.isArray(d.warnings) ? (d.warnings as string[]) : []} />
          </>
        )}

        {kind === 'store' && (
          <>
            {str(d.detail) && (
              <Section title={d.mode === 'query' ? 'Query' : 'Stored'}>
                <div className={clsx(VALUE_CLS, 'break-words')}>{str(d.detail)}</div>
              </Section>
            )}
            <ItemList title={d.mode === 'query' ? 'Facts' : 'Entities'} items={Array.isArray(d.items) ? (d.items as string[]) : []} />
          </>
        )}
      </div>

      {(run?.assistantMessageId && onShowInChat) || profileUrl ? (
        <div className="flex flex-wrap gap-1.5 px-3.5 py-2.5 border-t border-[var(--glass-stroke)]">
          {run?.assistantMessageId && onShowInChat && (
            <button
              type="button"
              onClick={() => onShowInChat(run.assistantMessageId!)}
              className="pill-btn-outline text-[0.7rem] inline-flex items-center gap-1"
            >
              <MessageSquare className="w-3 h-3" />
              Show in chat
            </button>
          )}
          {profileUrl && (
            <a href={profileUrl} target="_blank" rel="noopener noreferrer" className="pill-btn-outline text-[0.7rem] inline-flex items-center gap-1">
              <ExternalLink className="w-3 h-3" />
              Open link
            </a>
          )}
        </div>
      ) : null}
      </div>
    </aside>
  )
}

function ItemList({ title, items, mono = false }: { title: string; items: string[]; mono?: boolean }) {
  const list = items.filter(Boolean)
  if (list.length === 0) return null
  return (
    <Section title={title}>
      <ul className="space-y-0.5">
        {list.slice(0, 16).map((t, i) => (
          <li key={i} className={clsx(VALUE_CLS, 'text-[0.72rem] break-words', mono && 'font-mono text-[0.68rem]')}>
            {t}
          </li>
        ))}
        {list.length > 16 && <li className="text-[0.68rem] text-[var(--text-muted)]">+{list.length - 16} more</li>}
      </ul>
    </Section>
  )
}

const SOURCE_LABEL: Record<string, string> = { laya: 'Laya', rules: 'Rules', probe: 'Probe', config: 'Config', user: 'You' }

function DecisionsDetail({ decisions }: { decisions: Decision[] }) {
  const list = decisions.filter(d => d && typeof d === 'object')
  if (list.length === 0) return <div className="text-[0.72rem] text-[var(--text-muted)]">No decisions recorded.</div>
  return (
    <div className="space-y-2.5">
      {list.map((dec, i) => {
        const probs = dec.probabilities && typeof dec.probabilities === 'object' ? Object.entries(dec.probabilities) : []
        return (
          <div key={`${dec.id}-${i}`} className="rounded-xl border border-[var(--glass-stroke)] px-2.5 py-2">
            <div className="flex items-center gap-1.5">
              <span className="text-[0.68rem] font-semibold text-[var(--text-secondary)] truncate flex-1">{dec.label || dec.id}</span>
              {dec.source && (
                <span className="text-[0.55rem] uppercase font-bold tracking-wider px-1.5 py-px rounded-full bg-[var(--glass-fill-hover)] text-[var(--text-muted)]">
                  {SOURCE_LABEL[dec.source] || dec.source}
                </span>
              )}
            </div>
            <div className="flex items-baseline gap-2 mt-0.5">
              <span className="text-[0.8rem] font-semibold text-[var(--text-primary)] break-all">{dec.value || '—'}</span>
              {typeof dec.confidence === 'number' && (
                <span className="text-[0.66rem] font-mono text-[var(--text-muted)]">{Math.round(dec.confidence * 100)}%</span>
              )}
            </div>
            {dec.note && <div className="text-[0.68rem] text-[var(--text-muted)] mt-0.5 leading-snug">{dec.note}</div>}
            {probs.length > 0 && (
              <div className="mt-1.5 space-y-0.5">
                {probs
                  .sort((a, b) => Number(b[1]) - Number(a[1]))
                  .slice(0, 5)
                  .map(([label, p]) => (
                    <div key={label} className="flex items-center gap-1.5 text-[0.64rem]">
                      <span className="w-24 truncate text-[var(--text-secondary)]">{label}</span>
                      <span className="flex-1 h-1 rounded-full bg-[var(--glass-fill-hover)] overflow-hidden">
                        <span className="block h-full rounded-full bg-[var(--gold)]" style={{ width: `${Math.max(0, Math.min(100, Number(p) * 100))}%` }} />
                      </span>
                      <span className="w-8 text-right font-mono text-[var(--text-muted)]">{Math.round(Number(p) * 100)}%</span>
                    </div>
                  ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
