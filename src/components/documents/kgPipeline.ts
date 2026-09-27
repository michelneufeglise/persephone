import { MarkerType, type Node, type Edge } from '@xyflow/react'
import type { Tile, KGGraph } from '@/lib/docAgent'

/**
 * Pure builder for the Knowledge-graph "Pipeline" view: turns ONE doc-agent
 * run (its tiles) plus the knowledge store snapshot into a GraphRAG-style
 * architecture diagram (Question → Encoder → Tool selection → Context → LLM →
 * Answer, a tools row, and the knowledge-store band underneath).
 *
 * Canvas space is roughly 1190 × 985. Every node carries explicit width/height,
 * and every edge names its source/target handle (`s-<side>` / `t-<side>`, see
 * PipelineNodeComponent) so arrows leave and enter from sensible sides.
 */

export type PipelineRole =
  | 'question'
  | 'answer'
  | 'encoder'
  | 'toolselect'
  | 'instruction'
  | 'context'
  | 'llm'
  | 'tool'
  | 'graph'
  | 'source'
  | 'band'

interface PipelineNodeFields {
  /** Main title rendered on the card. */
  label: string
  /** Visual role (drives colour + layout). */
  role: PipelineRole
  /** Small uppercase label above the title. */
  kindLabel: string
  details?: string[]
  /** Decision chips (Tool selection). */
  chips?: string[]
  /** Only set on tool nodes: whether this run used the tool. */
  used?: boolean
  /** Max detail lines to show (default 3). */
  maxDetails?: number
  /** Full, untruncated text (question / answer) for the detail card. */
  fullText?: string
  /** Band only: stats shown on the right of the band title. */
  stats?: string
}

export interface PipelineNodeData extends PipelineNodeFields, Record<string, unknown> {
  /** Always 'pipeline' — keeps DetailCard's other-view branches from matching. */
  kind: 'pipeline'
}

export interface PipelineRun {
  question: string
  tiles: Tile[]
  answer: string
  intent?: string
}

type Side = 't' | 'r' | 'b' | 'l'

// ── Colours / edge styles ────────────────────────────────────────────────────
const ACTIVE = 'var(--accent)'
const IDLE = 'var(--text-muted)'
const STORE = '#fb923c'
const DASH = '6 5'

// ── Layout (top-left corners) ────────────────────────────────────────────────
const W = 200
const COL = [20, 270, 520] as const
const LLM_X = 770
const ANSWER_X = 990
const ROW_Q = 20
const ROW_INSTR = 150
const ROW_MID = 280
const MID_H = 132
const ROW_TOOLS = 468
const TOOL_H = 120
const BAND_Y = 630
const GRAPH_Y = 690
const SRC_Y = 925

// ── Small helpers ────────────────────────────────────────────────────────────
const fmt = (n: number) => n.toLocaleString('en-US')
const plural = (n: number, one: string, many = `${one}s`) => `${fmt(n)} ${n === 1 ? one : many}`

function clip(text: string, max: number): string {
  const t = text.replace(/\s+/g, ' ').trim()
  return t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t
}

/** Strip the most common markdown noise for a one-line preview. */
function plainPreview(md: string, max: number): string {
  return clip(md.replace(/[#*_`>|]+/g, ' ').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1'), max)
}

/** `hf.co/org/Some-Model:q4` → `Some-Model:q4` */
function shortModel(model: string | null | undefined): string {
  if (!model) return ''
  const parts = model.split('/')
  return parts[parts.length - 1] || model
}

function isUsed(t: Tile | undefined): boolean {
  return !!t && t.status !== 'skipped'
}

function charsOf(t: Tile): number {
  const m = /\((\d[\d,]*)\s*chars?\)/i.exec(t.detail || '')
  return m ? Number(m[1].replace(/,/g, '')) : 0
}

export function buildPipeline(
  run: PipelineRun | null,
  kg: KGGraph | null,
): { nodes: Node<PipelineNodeData>[]; edges: Edge[] } {
  const nodes: Node<PipelineNodeData>[] = []
  const edges: Edge[] = []
  if (!run) return { nodes, edges }

  const { question, tiles = [], answer, intent: runIntent } = run

  // ── Tile lookup (by id first, then by kind) ────────────────────────────────
  const byId = (id: string) => tiles.find(t => t.id === id)
  const byKind = (...kinds: string[]) => tiles.find(t => kinds.includes(t.kind))
  const laya = byId('laya') ?? byKind('laya')
  const answerTile = byId('answer') ?? byKind('llm', 'vision')
  const webTile = byId('web-search') ?? byKind('web')
  const planTile = byId('web-plan') ?? byKind('planner')
  const queryTile = byId('kg-query') ?? byKind('query')
  const ingestTile = byId('kg-ingest') ?? byKind('store')
  const extractTiles = tiles.filter(
    t => t.id.startsWith('extract-') || t.kind === 'extract' || t.kind === 'ocr',
  )

  const decisions = laya?.decisions ?? []
  const decision = (id: string) => decisions.find(d => d.id === id)
  const intentDecision = decision('intent')
  const intent = runIntent || intentDecision?.value || ''

  const extractUsed = extractTiles.some(isUsed)
  const webUsed = isUsed(webTile)
  const queryUsed = isUsed(queryTile)

  // ── Builders ───────────────────────────────────────────────────────────────
  const addNode = (
    id: string,
    x: number,
    y: number,
    width: number,
    height: number,
    data: PipelineNodeFields,
    extra: Partial<Node<PipelineNodeData>> = {},
  ) => {
    nodes.push({
      id,
      type: 'pipeline',
      position: { x, y },
      width,
      height,
      style: { width, height },
      draggable: false,
      data: { ...data, kind: 'pipeline' },
      ...extra,
    })
  }

  const addEdge = (
    source: string,
    sourceSide: Side,
    target: string,
    targetSide: Side,
    opts: { used: boolean; curve?: boolean; label?: string; store?: boolean },
  ) => {
    const { used, curve, label, store } = opts
    const color = store ? STORE : used ? ACTIVE : IDLE
    const marker = { type: MarkerType.ArrowClosed, width: 16, height: 16, color }
    edges.push({
      id: `${source}->${target}`,
      source,
      target,
      sourceHandle: `s-${sourceSide}`,
      targetHandle: `t-${targetSide}`,
      type: curve ? 'default' : 'smoothstep',
      className: `kg-edge ${store ? 'kg-edge-store' : used ? 'kg-edge-active' : 'kg-edge-idle'}`,
      animated: used && !store,
      label,
      markerEnd: marker,
      ...(store ? { markerStart: marker } : {}),
      style: {
        stroke: color,
        strokeWidth: used || store ? 2 : 1.4,
        strokeDasharray: DASH,
        opacity: used || store ? 0.95 : 0.45,
      },
      data: { used },
    })
  }

  // ── Question / Answer / LLM ───────────────────────────────────────────────
  addNode('question', COL[0], ROW_Q, W + 20, 88, {
    role: 'question',
    kindLabel: 'Question',
    label: clip(question || '—', 110),
    fullText: question,
  })

  const llmModel = answerTile?.model || decision('answer_model')?.value || 'LLM'
  const llmDetails: string[] = []
  if (answerTile?.kind === 'vision') llmDetails.push('vision model')
  else llmDetails.push('chat model')
  if (answerTile?.ms) llmDetails.push(`${(answerTile.ms / 1000).toFixed(1)} s`)
  if (answerTile && answerTile.status !== 'done') llmDetails.push(answerTile.status)
  addNode('llm', LLM_X, 130, 170, ROW_MID + MID_H - 130, {
    role: 'llm',
    kindLabel: 'LLM',
    label: shortModel(llmModel),
    details: llmDetails,
    fullText: llmModel,
  })

  addNode('answer', ANSWER_X, 210, W, 116, {
    role: 'answer',
    kindLabel: 'Answer',
    label: answer ? plainPreview(answer, 80) : '…',
    fullText: answer,
  })

  // ── Encoder (Laya) ─────────────────────────────────────────────────────────
  const encoderDetails: string[] = []
  if (intent) encoderDetails.push(intent.replace(/_/g, ' '))
  if (intentDecision?.confidence != null) {
    encoderDetails.push(`confidence ${Math.round(intentDecision.confidence * 100)}%`)
  } else if (intentDecision?.source) {
    encoderDetails.push(`via ${intentDecision.source}`)
  }
  addNode('encoder', COL[0], ROW_MID, W, MID_H, {
    role: 'encoder',
    kindLabel: 'Encoder',
    label: 'Laya',
    details: encoderDetails,
  })

  // ── Tool selection ────────────────────────────────────────────────────────
  const chips: string[] = []
  if (intent) chips.push(`intent: ${intent}`)
  const web = decision('web_lookup')
  if (web) chips.push(`web: ${!web.value || web.value === 'none' ? 'off' : web.value}`)
  const tool = decision('tool')
  if (tool) chips.push(`tool: ${tool.value}`)
  const answerModel = decision('answer_model')
  if (answerModel) chips.push(`model: ${shortModel(answerModel.value)}`)
  addNode('toolselect', COL[1], ROW_MID, W, MID_H, {
    role: 'toolselect',
    kindLabel: 'Router',
    label: 'Tool selection',
    chips: chips.slice(0, 4),
  })

  // ── Instruction ───────────────────────────────────────────────────────────
  const roleLines = decisions
    .filter(d => d.id.startsWith('role-'))
    .map(d => {
      const m = /\((.*)\)/.exec(d.label)
      return `${clip(m ? m[1] : d.label, 22)} → ${d.value}`
    })
  addNode('instruction', COL[2], ROW_INSTR, W, 88, {
    role: 'instruction',
    kindLabel: 'Instruction',
    label: `${(intent || 'general').replace(/_/g, ' ')} prompt`,
    details: roleLines.slice(0, 2),
  })

  // ── Context ───────────────────────────────────────────────────────────────
  const contextDetails: string[] = []
  const usedExtracts = extractTiles.filter(isUsed)
  const totalChars = usedExtracts.reduce((s, t) => s + charsOf(t), 0)
  if (usedExtracts.length === 1) {
    const name = usedExtracts[0].doc?.name
    contextDetails.push(
      totalChars
        ? `${fmt(totalChars)} chars${name ? ` from ${name}` : ''}`
        : `text from ${name || '1 document'}`,
    )
  } else if (usedExtracts.length > 1) {
    contextDetails.push(`${fmt(totalChars)} chars from ${usedExtracts.length} docs`)
  }
  let webResults = 0
  let webSearches = 0
  if (webTile) {
    const d = webTile.detail || ''
    const mRes = /(\d+)\s+matching/i.exec(d)
    const mSearch = /(\d+)\s+search/i.exec(d)
    const items = webTile.items ?? []
    webResults = mRes ? Number(mRes[1]) : items.filter(i => i.kind === 'result').length
    webSearches = mSearch ? Number(mSearch[1]) : items.filter(i => i.kind === 'query').length
    if (webUsed) contextDetails.push(`+ ${plural(webResults, 'web result')}`)
  }
  const facts = queryTile ? (queryTile.items ?? []).length : 0
  if (queryUsed) contextDetails.push(facts ? `+ ${plural(facts, 'fact')}` : `+ graph lookup`)
  if (contextDetails.length === 0) contextDetails.push('question only')
  addNode('context', COL[2], ROW_MID, W, MID_H, {
    role: 'context',
    kindLabel: 'Retrieved',
    label: 'Context',
    details: contextDetails,
  })

  // ── Tools row ─────────────────────────────────────────────────────────────
  const webDetails: string[] = []
  if (webUsed && webTile) {
    if (webTile.model) webDetails.push(shortModel(webTile.model))
    webDetails.push(`${plural(webSearches, 'search', 'searches')} · ${plural(webResults, 'match', 'matches')}`)
    if (planTile?.detail) webDetails.push(planTile.detail)
  } else {
    webDetails.push('web lookup (LinkedIn, …)')
  }
  addNode('tool-web', COL[0], ROW_TOOLS, W, TOOL_H, {
    role: 'tool',
    kindLabel: 'Tool · web',
    label: 'Search',
    details: webDetails,
    used: webUsed,
  })

  const extractDetails: string[] = []
  if (extractUsed) {
    extractDetails.push(
      `${plural(usedExtracts.length, 'doc')}${totalChars ? ` · ${fmt(totalChars)} chars` : ''}`,
    )
    const ocr = usedExtracts.find(t => t.kind === 'ocr')
    if (ocr) extractDetails.push(`OCR${ocr.model ? ` · ${shortModel(ocr.model)}` : ''}`)
    else if (usedExtracts.some(t => /ocr skipped/i.test(t.detail || ''))) extractDetails.push('text layer · OCR skipped')
    const firstName = usedExtracts[0].doc?.name
    if (firstName) extractDetails.push(firstName)
  } else {
    extractDetails.push('read / OCR documents')
  }
  addNode('tool-extract', COL[1], ROW_TOOLS, W, TOOL_H, {
    role: 'tool',
    kindLabel: 'Tool · documents',
    label: 'Search + Pattern match',
    details: extractDetails,
    used: extractUsed,
  })

  const queryDetails: string[] = []
  if (queryUsed && queryTile) {
    if (queryTile.detail) queryDetails.push(queryTile.detail)
    if (facts) queryDetails.push(plural(facts, 'fact'))
    if (queryDetails.length === 0) queryDetails.push('knowledge-graph lookup')
  } else {
    queryDetails.push('knowledge-graph lookup')
  }
  addNode('tool-query', COL[2], ROW_TOOLS, W, TOOL_H, {
    role: 'tool',
    kindLabel: 'Tool · graph',
    label: 'Query',
    details: queryDetails,
    used: queryUsed,
  })

  // ── Knowledge store band (background) ─────────────────────────────────────
  const stats = kg?.stats
  const bandStats = stats
    ? `${plural(stats.entities, 'entity', 'entities')} · ${plural(stats.relations, 'relation')} · ${plural(stats.documents, 'document')}`
    : 'loading…'
  addNode(
    'kg-band',
    0,
    BAND_Y,
    760,
    230,
    { role: 'band', kindLabel: 'Graph database', label: 'Knowledge store', stats: bandStats },
    { type: 'pipelineGroup', zIndex: -1, selectable: false, focusable: false },
  )

  const docs = kg?.documents ?? []
  const lexicalDetails = docs.slice(0, 3).map(d => d.name)
  if (stats) lexicalDetails.push(plural(stats.chunks, 'chunk'))
  if (lexicalDetails.length === 0) lexicalDetails.push('no documents yet')
  addNode('kg-lexical', 40, GRAPH_Y, W, 138, {
    role: 'graph',
    kindLabel: 'Graph · chunks',
    label: 'Lexical graph',
    details: lexicalDetails,
    maxDetails: 4,
  })

  const topEntities = [...(kg?.entities ?? [])]
    .sort((a, b) => (b.mention_count ?? 0) - (a.mention_count ?? 0))
    .slice(0, 4)
    .map(e => `${e.name} · ${e.type}`)
  addNode('kg-domain', 520, GRAPH_Y, W, 138, {
    role: 'graph',
    kindLabel: 'Graph · entities',
    label: 'Domain graph',
    details: topEntities.length ? topEntities : ['empty — ask about a document'],
    maxDetails: 4,
  })

  addNode('source-docs', 40, SRC_Y, W, 78, {
    role: 'source',
    kindLabel: 'Source',
    label: 'Documents',
    details: [stats ? plural(stats.documents, 'document') : 'uploaded files'],
    maxDetails: 1,
  })
  addNode('source-data', 520, SRC_Y, W, 78, {
    role: 'source',
    kindLabel: 'Source',
    label: 'Data',
    details: ['web lookups · learned facts'],
    maxDetails: 1,
  })

  // ── Edges: main path ──────────────────────────────────────────────────────
  addEdge('question', 'r', 'llm', 't', { used: true })
  addEdge('question', 'b', 'encoder', 't', { used: true })
  addEdge('encoder', 'r', 'toolselect', 'l', { used: true })
  addEdge('toolselect', 'r', 'context', 'l', { used: true })
  addEdge('instruction', 'r', 'llm', 'l', { used: true })
  addEdge('context', 'r', 'llm', 'l', { used: true })
  addEdge('llm', 'r', 'answer', 'l', { used: true })

  // Tools → Tool selection (curved)
  addEdge('tool-web', 't', 'toolselect', 'b', { used: webUsed, curve: true })
  addEdge('tool-extract', 't', 'toolselect', 'b', { used: extractUsed, curve: true })
  addEdge('tool-query', 't', 'toolselect', 'b', { used: queryUsed, curve: true })

  // Knowledge store → tools
  addEdge('kg-lexical', 't', 'tool-web', 'b', { used: webUsed, curve: true })
  addEdge('kg-lexical', 't', 'tool-extract', 'b', { used: extractUsed, curve: true })
  addEdge('kg-domain', 't', 'tool-extract', 'b', { used: extractUsed && queryUsed, curve: true })
  addEdge('kg-domain', 't', 'tool-query', 'b', { used: queryUsed, curve: true })

  // Lexical ⟷ Domain (orange, double-headed)
  addEdge('kg-lexical', 'r', 'kg-domain', 'l', { used: false, store: true })

  // Sources → graphs
  addEdge('source-docs', 't', 'kg-lexical', 'b', { used: extractUsed })
  addEdge('source-data', 't', 'kg-domain', 'b', { used: isUsed(ingestTile) || queryUsed })

  // Answer → Domain graph when this run taught the store something
  if (ingestTile && ingestTile.status === 'done') {
    const m = /\+(\d+)\s+entit/i.exec(ingestTile.detail || '')
    const n = m ? Number(m[1]) : 0
    addEdge('answer', 'b', 'kg-domain', 'r', { used: true, label: n ? `learned +${n}` : 'learned' })
  }

  return { nodes, edges }
}
