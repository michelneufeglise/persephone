/**
 * Structured (ELK layered) layout for the Knowledge graph Network view.
 *
 * Columns are fixed with ELK partitions (0 Documents · 1 Questions · 2 Laya
 * decisions · 3 Models · 4 Results); edges are routed orthogonally and the
 * routes (start → bend points → end) are returned so the view can draw them
 * as rounded polylines. Pure / async — no React — so it runs in Node too.
 */
import type { ElkNode, ElkExtendedEdge } from 'elkjs/lib/elk-api'
import { runNodeSize, type RunGraph, type RunNode } from './kgNetwork'

export interface Pt {
  x: number
  y: number
}

export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

/** Background panel behind one run: a left part (question + decisions) and a right part (results). */
export interface RunBand {
  runKey: string
  /** Absolute rects. */
  parts: { rect: Rect; role: 'main' | 'results' }[]
  bbox: Rect
}

export interface ElkLayoutResult {
  positions: Record<string, Rect>
  routes: Record<string, Pt[]>
  bands: RunBand[]
  width: number
  height: number
}

interface ElkLike {
  layout(graph: ElkNode): Promise<ElkNode>
}
let elkPromise: Promise<ElkLike> | null = null
/** ELK (~1.5 MB) is loaded on first use, so it stays out of the main bundle. */
function loadElk(): Promise<ElkLike> {
  if (!elkPromise) {
    elkPromise = import('elkjs/lib/elk.bundled.js').then(m => new m.default() as unknown as ElkLike)
    elkPromise.catch(() => {
      elkPromise = null
    })
  }
  return elkPromise
}

export const ELK_LAYOUT_OPTIONS: Record<string, string> = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',
  'elk.edgeRouting': 'ORTHOGONAL',
  'elk.partitioning.activate': 'true',
  'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
  'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
  'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
  // Edges into a shared model node share their final run (a bus), which removes
  // most fan-vs-fan crossings (10-run test: 77 → 10).
  'elk.layered.mergeEdges': 'true',
  // Runs stay in chronological order (latest on top) in every column — predictable
  // reading order beats the last few crossings (10-run test: 8 → 14, force layout: 88).
  'elk.layered.crossingMinimization.forceNodeModelOrder': 'true',
  // Keep every column in run order so each run's cards stay together (clean run panels).
  
  'elk.spacing.nodeNode': '44',
  'elk.layered.spacing.nodeNodeBetweenLayers': '62',
  'elk.spacing.edgeNode': '20',
  'elk.layered.spacing.edgeNodeBetweenLayers': '24',
  'elk.spacing.edgeEdge': '12',
  'elk.layered.spacing.edgeEdgeBetweenLayers': '12',
  'elk.separateConnectedComponents': 'false',
  'elk.padding': '[top=32,left=16,bottom=16,right=16]',
}

/**
 * Model order (= vertical order inside every column): latest run first; shared
 * documents / models sit next to the latest run that uses them.
 */
function runRanks(graph: RunGraph): Map<string, number> {
  return new Map(graph.runs.map(r => [r.key, graph.runs.length - 1 - r.index]))
}

function orderedNodes(graph: RunGraph, nodes: RunNode[]): RunNode[] {
  const runIndex = runRanks(graph)
  const firstUse = new Map<string, number>()
  for (const e of graph.edges) {
    const idx = runIndex.get(e.runKey) ?? 0
    for (const id of [e.source, e.target]) {
      if (!firstUse.has(id) || firstUse.get(id)! > idx) firstUse.set(id, idx)
    }
  }
  const rank = (n: RunNode) => (n.runKey ? runIndex.get(n.runKey) ?? 0 : firstUse.get(n.id) ?? 0)
  return nodes
    .map((n, i) => ({ n, i }))
    .sort((a, b) => a.n.column - b.n.column || rank(a.n) - rank(b.n) || a.i - b.i)
    .map(x => x.n)
}

const RESULTS_BLOCK_OPTIONS: Record<string, string> = {
  'elk.algorithm': 'layered',
  'elk.direction': 'RIGHT',
  'elk.edgeRouting': 'ORTHOGONAL',
  'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
  'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
  'elk.spacing.nodeNode': '16',
  'elk.layered.spacing.nodeNodeBetweenLayers': '48',
  'elk.spacing.edgeNode': '14',
  'elk.spacing.edgeEdge': '10',
  'elk.padding': '[top=0,left=0,bottom=0,right=0]',
}

interface Block {
  id: string
  runKey: string
  role: 'main' | 'results'
  nodeIds: string[]
  /** Positions / routes relative to the block's top-left. */
  positions: Record<string, Rect>
  routes: Record<string, Pt[]>
  width: number
  height: number
}

const MAIN_BLOCK_GAP = 90

/** Question → decisions side by side, centre-aligned (straight q→dec edge). */
function layoutMainBlock(graph: RunGraph, runKey: string, nodeIds: string[]): Block {
  const nodeById = new Map(graph.nodes.map(n => [n.id, n]))
  const q = nodeIds.map(id => nodeById.get(id)!).find(n => n.kind === 'question')!
  const dec = nodeIds.map(id => nodeById.get(id)!).find(n => n.kind === 'decisions')
  const qs = runNodeSize(q)
  const positions: Record<string, Rect> = {}
  const routes: Record<string, Pt[]> = {}
  if (!dec) {
    positions[q.id] = { x: 0, y: 0, ...qs }
    return { id: `run-main:${runKey}`, runKey, role: 'main', nodeIds, positions, routes, width: qs.width, height: qs.height }
  }
  const ds = runNodeSize(dec)
  const h = Math.max(qs.height, ds.height)
  positions[q.id] = { x: 0, y: (h - qs.height) / 2, ...qs }
  positions[dec.id] = { x: qs.width + MAIN_BLOCK_GAP, y: (h - ds.height) / 2, ...ds }
  for (const e of graph.edges) {
    if (e.source === q.id && e.target === dec.id) {
      routes[e.id] = [
        { x: qs.width, y: h / 2 },
        { x: qs.width + MAIN_BLOCK_GAP, y: h / 2 },
      ]
    }
  }
  return { id: `run-main:${runKey}`, runKey, role: 'main', nodeIds, positions, routes, width: qs.width + MAIN_BLOCK_GAP + ds.width, height: h }
}

/** One run's results (planner → web → profiles / store) as a compact block (small ELK run). */
async function layoutResultsBlock(graph: RunGraph, runKey: string, nodeIds: string[]): Promise<Block> {
  const inBlock = new Set(nodeIds)
  const nodeById = new Map(graph.nodes.map(n => [n.id, n]))
  const internal = graph.edges.filter(e => inBlock.has(e.source) && inBlock.has(e.target))
  const out = await (await loadElk()).layout({
    id: `block:${runKey}`,
    layoutOptions: RESULTS_BLOCK_OPTIONS,
    children: nodeIds.map(id => ({ id, ...runNodeSize(nodeById.get(id)!) })),
    edges: internal.map(e => ({ id: e.id, sources: [e.source], targets: [e.target] })) as ElkExtendedEdge[],
  })
  const positions: Record<string, Rect> = {}
  let w = 0
  let h = 0
  for (const c of out.children || []) {
    positions[c.id] = { x: c.x ?? 0, y: c.y ?? 0, width: c.width ?? 0, height: c.height ?? 0 }
    w = Math.max(w, (c.x ?? 0) + (c.width ?? 0))
    h = Math.max(h, (c.y ?? 0) + (c.height ?? 0))
  }
  return { id: `run-results:${runKey}`, runKey, role: 'results', nodeIds, positions, routes: sectionsToRoutes((out.edges || []) as ElkExtendedEdge[]), width: w, height: h }
}

function sectionsToRoutes(edges: ElkExtendedEdge[]): Record<string, Pt[]> {
  const routes: Record<string, Pt[]> = {}
  for (const e of edges) {
    const pts: Pt[] = []
    for (const s of e.sections || []) {
      if (pts.length === 0) pts.push({ x: s.startPoint.x, y: s.startPoint.y })
      for (const b of s.bendPoints || []) pts.push({ x: b.x, y: b.y })
      pts.push({ x: s.endPoint.x, y: s.endPoint.y })
    }
    if (pts.length >= 2) routes[e.id] = pts
  }
  return routes
}

/**
 * Two-stage layout:
 *  1. each expanded run becomes two rigid blocks — main (question → decisions,
 *     side by side) and results (planner → web → profiles / store, a small ELK
 *     run of its own);
 *  2. the main graph (shared documents + models, compact runs and the blocks as
 *     single nodes with fixed WEST/EAST ports at their entry / exit cards) is laid
 *     out with partitions: 0 Documents · 1 Questions+Decisions · 3 Models · 4 Results.
 * Block contents are then translated into place, so a run's cards always stay
 * together (clean run panels), and cross-block edges end exactly on the cards.
 */
export async function layoutRunGraphElk(graph: RunGraph): Promise<ElkLayoutResult> {
  const idsByRunRole = new Map<string, { main: string[]; results: string[] }>()
  for (const n of graph.nodes) {
    if (!n.runKey || n.kind === 'runCompact') continue
    const entry = idsByRunRole.get(n.runKey) || { main: [], results: [] }
    if (n.column === 4) entry.results.push(n.id)
    else if (n.kind === 'question' || n.kind === 'decisions') entry.main.push(n.id)
    idsByRunRole.set(n.runKey, entry)
  }
  const blocks: Block[] = []
  const pending: Promise<Block>[] = []
  for (const [key, { main, results }] of idsByRunRole) {
    if (main.length) blocks.push(layoutMainBlock(graph, key, main))
    if (results.length) pending.push(layoutResultsBlock(graph, key, results))
  }
  blocks.push(...(await Promise.all(pending)))
  const blockOf = new Map<string, Block>()
  for (const b of blocks) for (const id of b.nodeIds) blockOf.set(id, b)

  const runIndex = runRanks(graph)
  const loose = orderedNodes(graph, graph.nodes.filter(n => !blockOf.has(n.id)))
  const orderedBlocks = blocks
  // Ports: entry (WEST, at the target card's left-centre) / exit (EAST, at the source card's right-centre).
  const portId = (b: Block, nodeId: string, side: 'in' | 'out') => `${b.id}:${side}:${nodeId}`
  const ports = new Map<string, { side: 'in' | 'out'; nodeId: string }[]>()
  const addPort = (b: Block, nodeId: string, side: 'in' | 'out') => {
    const list = ports.get(b.id) || []
    if (!list.some(p => p.nodeId === nodeId && p.side === side)) list.push({ side, nodeId })
    ports.set(b.id, list)
  }
  const edges: ElkExtendedEdge[] = []
  for (const e of graph.edges) {
    const sb = blockOf.get(e.source)
    const tb = blockOf.get(e.target)
    if (sb && tb && sb === tb) continue // internal to a block
    if (sb) addPort(sb, e.source, 'out')
    if (tb) addPort(tb, e.target, 'in')
    edges.push({
      id: e.id,
      sources: [sb ? portId(sb, e.source, 'out') : e.source],
      targets: [tb ? portId(tb, e.target, 'in') : e.target],
    })
  }
  // Partitions: 0 Documents · 1 Questions (+ decisions inside the main blocks) · 3 Models · 4 Results.
  // Model order inside a column = latest run first (shared nodes: by the latest run using them).
  const partitionOf = (n: RunNode) => (n.column === 2 ? 1 : n.column)
  const looseRank = new Map(loose.map((n, i) => [n.id, i]))
  const items: { rank: number; col: number; node: ElkNode }[] = []
  for (const n of loose) {
    const col = partitionOf(n)
    items.push({
      rank: n.runKey ? runIndex.get(n.runKey) ?? 0 : looseRank.get(n.id) ?? 0,
      col,
      node: { id: n.id, ...runNodeSize(n), layoutOptions: { 'elk.partitioning.partition': String(col), 'elk.alignment': 'LEFT' } },
    })
  }
  for (const b of orderedBlocks) {
    const col = b.role === 'main' ? 1 : 4
    items.push({
      rank: runIndex.get(b.runKey) ?? 0,
      col,
      node: {
        id: b.id,
        width: b.width,
        height: b.height,
        layoutOptions: { 'elk.partitioning.partition': String(col), 'elk.portConstraints': 'FIXED_POS', 'elk.alignment': 'LEFT' },
        ports: (ports.get(b.id) || []).map(p => {
          const r = b.positions[p.nodeId]
          return p.side === 'in'
            ? { id: portId(b, p.nodeId, 'in'), x: r.x - 1, y: r.y + r.height / 2, width: 1, height: 1, layoutOptions: { 'elk.port.side': 'WEST' } }
            : { id: portId(b, p.nodeId, 'out'), x: r.x + r.width, y: r.y + r.height / 2, width: 1, height: 1, layoutOptions: { 'elk.port.side': 'EAST' } }
        }),
      },
    })
  }
  items.sort((x, y) => x.col - y.col || x.rank - y.rank)
  const children: ElkNode[] = items.map(it => it.node)

  const root: ElkNode = { id: 'root', layoutOptions: ELK_LAYOUT_OPTIONS, children, edges }
  const out: ElkNode = await (await loadElk()).layout(root)

  const positions: Record<string, Rect> = {}
  const blockRects: Record<string, Rect> = {}
  const blockIds = new Set(blocks.map(b => b.id))
  for (const c of out.children || []) {
    const rect = { x: c.x ?? 0, y: c.y ?? 0, width: c.width ?? 0, height: c.height ?? 0 }
    if (blockIds.has(c.id)) blockRects[c.id] = rect
    else positions[c.id] = rect
  }
  const routes = sectionsToRoutes((out.edges || []) as ElkExtendedEdge[])
  for (const b of blocks) {
    const origin = blockRects[b.id]
    if (!origin) continue
    for (const id of b.nodeIds) {
      const r = b.positions[id]
      positions[id] = { x: origin.x + r.x, y: origin.y + r.y, width: r.width, height: r.height }
    }
    for (const [id, pts] of Object.entries(b.routes)) routes[id] = pts.map(p => ({ x: origin.x + p.x, y: origin.y + p.y }))
  }
  // Snap cross-block edge ends from the 1px ports onto the card edges.
  for (const e of graph.edges) {
    const pts = routes[e.id]
    if (!pts || pts.length < 2) continue
    const sb = blockOf.get(e.source)
    const tb = blockOf.get(e.target)
    if (sb && tb && sb === tb) continue
    if (tb) {
      const t = positions[e.target]
      if (t) pts[pts.length - 1] = { x: t.x, y: pts[pts.length - 1].y }
    }
    if (sb) {
      const s0 = positions[e.source]
      if (s0) pts[0] = { x: s0.x + s0.width, y: pts[0].y }
    }
  }
  return {
    positions,
    routes,
    bands: computeRunBands(graph, positions),
    width: out.width ?? 0,
    height: out.height ?? 0,
  }
}

const BAND_PAD_X = 12
const BAND_PAD_TOP = 24
const BAND_PAD_BOTTOM = 10
const BAND_MIN_GAP = 6

/**
 * One background panel per run. The shared models column stays outside the
 * panels, so each band has a main part (question + decisions) and — when the
 * run has results — a results part. Overlapping parts of neighbouring runs are
 * split at the midpoint so panels never overlap each other.
 */
export function computeRunBands(graph: RunGraph, positions: Record<string, Rect>): RunBand[] {
  const nodeById = new Map(graph.nodes.map(n => [n.id, n]))
  const partsByRole: Record<'main' | 'results', { runKey: string; rect: Rect }[]> = { main: [], results: [] }
  for (const run of graph.runs) {
    for (const role of ['main', 'results'] as const) {
      const rects = run.nodeIds
        .filter(id => {
          const n = nodeById.get(id)
          return n && positions[id] && (role === 'main' ? n.column <= 2 : n.column === 4)
        })
        .map(id => positions[id])
      if (rects.length === 0) continue
      const minX = Math.min(...rects.map(r => r.x)) - BAND_PAD_X
      const maxX = Math.max(...rects.map(r => r.x + r.width)) + BAND_PAD_X
      const minY = Math.min(...rects.map(r => r.y)) - BAND_PAD_TOP
      const maxY = Math.max(...rects.map(r => r.y + r.height)) + BAND_PAD_BOTTOM
      partsByRole[role].push({ runKey: run.key, rect: { x: minX, y: minY, width: maxX - minX, height: maxY - minY } })
    }
  }
  // Resolve vertical overlaps among horizontally-overlapping parts of the same role.
  for (const role of ['main', 'results'] as const) {
    const parts = partsByRole[role].sort((a, b) => a.rect.y - b.rect.y)
    for (let i = 0; i < parts.length; i++) {
      for (let j = i + 1; j < parts.length; j++) {
        const a = parts[i].rect
        const b = parts[j].rect
        const xOverlap = a.x < b.x + b.width && b.x < a.x + a.width
        if (!xOverlap) continue
        const aBottom = a.y + a.height
        if (aBottom + BAND_MIN_GAP <= b.y) continue
        const mid = (aBottom + b.y) / 2
        a.height = Math.max(8, mid - BAND_MIN_GAP / 2 - a.y)
        const bBottom = b.y + b.height
        b.y = mid + BAND_MIN_GAP / 2
        b.height = Math.max(8, bBottom - b.y)
      }
    }
  }
  const bands: RunBand[] = []
  for (const run of graph.runs) {
    const parts = (['main', 'results'] as const).flatMap(role =>
      partsByRole[role].filter(p => p.runKey === run.key).map(p => ({ rect: p.rect, role })),
    )
    if (parts.length === 0) continue
    const minX = Math.min(...parts.map(p => p.rect.x))
    const minY = Math.min(...parts.map(p => p.rect.y))
    const maxX = Math.max(...parts.map(p => p.rect.x + p.rect.width))
    const maxY = Math.max(...parts.map(p => p.rect.y + p.rect.height))
    bands.push({ runKey: run.key, parts, bbox: { x: minX, y: minY, width: maxX - minX, height: maxY - minY } })
  }
  return bands
}

// ── Geometry helpers (used by the edge renderer and the verification script) ──

/** SVG path through the points with rounded corners of radius `r`. */
export function roundedPolylinePath(points: Pt[], r = 8): string {
  if (points.length === 0) return ''
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`
  let d = `M ${points[0].x} ${points[0].y}`
  for (let i = 1; i < points.length - 1; i++) {
    const p0 = points[i - 1]
    const p1 = points[i]
    const p2 = points[i + 1]
    const d1 = Math.hypot(p1.x - p0.x, p1.y - p0.y)
    const d2 = Math.hypot(p2.x - p1.x, p2.y - p1.y)
    const rr = Math.min(r, d1 / 2, d2 / 2)
    if (rr < 0.5) {
      d += ` L ${p1.x} ${p1.y}`
      continue
    }
    const ax = p1.x + ((p0.x - p1.x) / d1) * rr
    const ay = p1.y + ((p0.y - p1.y) / d1) * rr
    const bx = p1.x + ((p2.x - p1.x) / d2) * rr
    const by = p1.y + ((p2.y - p1.y) / d2) * rr
    d += ` L ${ax} ${ay} Q ${p1.x} ${p1.y} ${bx} ${by}`
  }
  const last = points[points.length - 1]
  d += ` L ${last.x} ${last.y}`
  return d
}

/** Midpoint of the longest segment — a calm place for an edge label. */
export function polylineLabelPoint(points: Pt[]): Pt {
  if (points.length < 2) return points[0] || { x: 0, y: 0 }
  let best = 0
  let bestLen = -1
  for (let i = 0; i < points.length - 1; i++) {
    const len = Math.hypot(points[i + 1].x - points[i].x, points[i + 1].y - points[i].y)
    if (len > bestLen) {
      bestLen = len
      best = i
    }
  }
  return { x: (points[best].x + points[best + 1].x) / 2, y: (points[best].y + points[best + 1].y) / 2 }
}

function segmentsCross(a1: Pt, a2: Pt, b1: Pt, b2: Pt): boolean {
  const eps = 1e-6
  const orient = (p: Pt, q: Pt, r: Pt) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)
  const o1 = orient(a1, a2, b1)
  const o2 = orient(a1, a2, b2)
  const o3 = orient(b1, b2, a1)
  const o4 = orient(b1, b2, a2)
  // Proper crossings only (touching / collinear overlaps — shared ports — don't count).
  return ((o1 > eps && o2 < -eps) || (o1 < -eps && o2 > eps)) && ((o3 > eps && o4 < -eps) || (o3 < -eps && o4 > eps))
}

/** Number of proper crossings between different polylines. */
export function countPolylineCrossings(lines: Pt[][]): number {
  let count = 0
  for (let i = 0; i < lines.length; i++) {
    for (let j = i + 1; j < lines.length; j++) {
      const a = lines[i]
      const b = lines[j]
      for (let s = 0; s < a.length - 1; s++) {
        for (let t = 0; t < b.length - 1; t++) {
          if (segmentsCross(a[s], a[s + 1], b[t], b[t + 1])) count++
        }
      }
    }
  }
  return count
}

export function rectsOverlap(a: Rect, b: Rect, gap = 0): boolean {
  return a.x < b.x + b.width + gap && b.x < a.x + a.width + gap && a.y < b.y + b.height + gap && b.y < a.y + a.height + gap
}
