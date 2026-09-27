/**
 * Run graph for the Knowledge graph "Network" and "Layers" views.
 *
 * Built straight from the doc-agent conversation messages (no React, no DOM), so
 * it can be unit-checked in Node. Compared to `buildKnowledgeGraph` (kgModel.ts,
 * still used by the Organic layout) it is decluttered:
 *   - every run's Laya decisions collapse into ONE "Laya decisions" card
 *   - documents and models are shared nodes (deduped by doc id / model name)
 *   - planner → web → profiles / knowledge-store results are chained per run
 *   - older runs can collapse into a single compact card
 *
 * Columns (ELK partitions in Network, fixed x in Layers):
 *   0 Documents · 1 Questions · 2 Laya decisions · 3 Models · 4 Results
 *
 * `layoutLanes` (Layers view) places the same graph as horizontal swimlanes:
 * one lane per run, shared nodes repeated per lane as lightweight reference
 * chips, so no edge ever leaves its lane.
 */
import type { Message } from '@/types'
import type { Tile, Decision, TileItem } from '@/lib/docAgent'
import { SOCIAL_PLATFORMS, isSocialProfileUrl, resolvePlatform } from './socialPlatforms'

export type RunNodeKind =
  | 'document'
  | 'question'
  | 'decisions'
  | 'model'
  | 'planner'
  | 'web'
  | 'profile'
  | 'store'
  | 'runCompact'

export type EdgeCategory = 'document' | 'decision' | 'model' | 'web' | 'store' | 'other'

export interface RunNode {
  id: string
  kind: RunNodeKind
  label: string
  data: Record<string, unknown>
  /** Owning run; null for shared nodes (documents, models). */
  runKey: string | null
  /** 0 Documents · 1 Questions · 2 Decisions · 3 Models · 4 Results */
  column: 0 | 1 | 2 | 3 | 4
}

export interface RunEdge {
  id: string
  source: string
  target: string
  label: string
  kind: string
  category: EdgeCategory
  /** The run this edge belongs to (every edge belongs to exactly one run). */
  runKey: string
  failed?: boolean
  /** Longer explanation (e.g. Laya's note on why this answer model). */
  title?: string
}

export interface RunInfo {
  /** Stable key: the assistant message id (or `u-<user message id>` while pending). */
  key: string
  /** Chronological index (0 = oldest). */
  index: number
  conversationId: string
  conversationTitle: string
  userMessageId: string | null
  assistantMessageId: string | null
  runId: string | null
  question: string
  timestamp: number | null
  status: 'done' | 'error' | 'cancelled' | 'streaming' | 'pending'
  answerModel: string | null
  intent: string | null
  collapsed: boolean
  /** Run-owned node ids (question, decisions, results — or the compact card). */
  nodeIds: string[]
  /** The run's primary node: question card, or the compact card when collapsed. */
  headId: string
}

export interface RunGraph {
  nodes: RunNode[]
  edges: RunEdge[]
  runs: RunInfo[]
}

export interface BuildRunGraphOptions {
  /** Collapse runs older than the latest `keepExpanded` into compact cards. */
  collapseOlder?: boolean
  keepExpanded?: number
  /** Run keys the user expanded explicitly (stay expanded while collapseOlder is on). */
  expandedRuns?: ReadonlySet<string>
  /** A message id (assistant or user) whose run is always expanded — the selected run. */
  forceExpandMessageId?: string | null
}

export interface DecisionChip {
  id: string
  text: string
  tone: 'intent' | 'web' | 'role' | 'model' | 'tool' | 'doc' | 'other'
}

// ── Sizes ────────────────────────────────────────────────────────────────────

export const RUN_NODE_SIZES: Record<Exclude<RunNodeKind, 'decisions'>, { width: number; height: number }> = {
  document: { width: 210, height: 60 },
  question: { width: 230, height: 72 },
  model: { width: 210, height: 60 },
  planner: { width: 210, height: 60 },
  web: { width: 210, height: 60 },
  profile: { width: 210, height: 60 },
  store: { width: 210, height: 60 },
  runCompact: { width: 230, height: 64 },
}
export const DECISIONS_CARD_WIDTH = 240
/** Lane reference chips (shared documents / models repeated per lane). */
export const REF_CHIP_SIZE = { width: 200, height: 44 }
export const MAX_DECISION_CHIPS = 8

const CHIP_FONT_PX = 6.1 // ~average glyph width at 10.5px
const CHIP_PAD_X = 16
const CHIP_H = 20
const CHIP_GAP = 4

function chipWidth(text: string, maxWidth: number): number {
  return Math.min(maxWidth, Math.ceil(text.length * CHIP_FONT_PX + CHIP_PAD_X))
}

/** Rows the chips wrap into inside a card of `innerWidth` (same estimate the card renders with). */
export function chipRows(chips: string[], innerWidth: number): number {
  if (chips.length === 0) return 0
  let rows = 1
  let x = 0
  for (const c of chips) {
    const w = chipWidth(c, innerWidth)
    if (x > 0 && x + CHIP_GAP + w > innerWidth) {
      rows++
      x = w
    } else {
      x += (x > 0 ? CHIP_GAP : 0) + w
    }
  }
  return rows
}

export function visibleDecisionChips(chips: DecisionChip[]): string[] {
  const texts = chips.slice(0, MAX_DECISION_CHIPS).map(c => c.text)
  if (chips.length > MAX_DECISION_CHIPS) texts.push(`+${chips.length - MAX_DECISION_CHIPS}`)
  return texts
}

export function decisionsCardHeight(chips: DecisionChip[]): number {
  const rows = chipRows(visibleDecisionChips(chips), DECISIONS_CARD_WIDTH - 24)
  // header (28) + chip rows + padding
  return Math.max(64, 34 + rows * CHIP_H + Math.max(0, rows - 1) * CHIP_GAP + 12)
}

export function runNodeSize(node: Pick<RunNode, 'kind' | 'data'>): { width: number; height: number } {
  if (node.kind === 'decisions') {
    return { width: DECISIONS_CARD_WIDTH, height: decisionsCardHeight((node.data.chips as DecisionChip[]) || []) }
  }
  return RUN_NODE_SIZES[node.kind] || RUN_NODE_SIZES.document
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function truncate(s: string, n: number): string {
  const t = (s || '').replace(/\s+/g, ' ').trim()
  return t.length > n ? t.slice(0, n - 1) + '…' : t
}

function inferDocKind(filename: string): string {
  const lower = filename.toLowerCase()
  if (lower.endsWith('.pdf')) return 'pdf'
  if (/\.(png|jpg|jpeg|gif|webp|svg)$/i.test(lower)) return 'image'
  if (/\.(eml|msg)$/i.test(lower)) return 'email'
  if (/\.(docx?|odt|rtf)$/i.test(lower)) return 'docx'
  if (/\.(xlsx?|csv|ods)$/i.test(lower)) return 'sheet'
  if (/\.(txt|md)$/i.test(lower)) return 'text'
  return 'other'
}

function pct(conf: unknown): string {
  return typeof conf === 'number' && Number.isFinite(conf) ? ` ${Math.round(conf * 100)}%` : ''
}

/** One chip per decision (roles deduped), in a stable, readable order. */
export function decisionChips(decisions: Decision[]): DecisionChip[] {
  const chips: DecisionChip[] = []
  const order = (d: Decision): number => {
    const id = d.id || ''
    if (id === 'intent') return 0
    if (id === 'web_lookup') return 1
    if (id === 'tool') return 2
    if (id.startsWith('role-')) return 3
    if (id.startsWith('doc_kind-')) return 4
    if (id.startsWith('ocr_needed-')) return 5
    if (id === 'answer_model') return 6
    if (id.startsWith('vision_fallback_')) return 7
    return 8
  }
  const sorted = [...decisions].filter(d => d && typeof d === 'object').sort((a, b) => order(a) - order(b))
  const roleCounts = new Map<string, number>()
  const docKinds = new Set<string>()
  for (const d of sorted) {
    const id = String(d.id || '')
    const value = d.value == null ? '' : String(d.value)
    if (id.startsWith('role-')) {
      roleCounts.set(value || '?', (roleCounts.get(value || '?') || 0) + 1)
      continue
    }
    if (id.startsWith('doc_kind-')) {
      if (value) docKinds.add(value)
      continue
    }
    if (id === 'intent') chips.push({ id, text: `${value || 'intent'}${pct(d.confidence)}`, tone: 'intent' })
    else if (id === 'web_lookup') {
      if (value && value !== 'none') chips.push({ id, text: `web: ${value}`, tone: 'web' })
    } else if (id === 'tool') chips.push({ id, text: `tool: ${value}`, tone: 'tool' })
    else if (id.startsWith('ocr_needed-')) chips.push({ id, text: 'OCR needed', tone: 'doc' })
    else if (id === 'answer_model') chips.push({ id, text: `model: ${value}`, tone: 'model' })
    else if (id.startsWith('vision_fallback_')) chips.push({ id, text: `fallback: ${value || 'none'}`, tone: 'model' })
    else chips.push({ id, text: `${d.label || id}: ${value}`, tone: 'other' })
  }
  // Roles + doc kinds go right after intent / web / tool.
  const insertAt = chips.findIndex(c => c.tone !== 'intent' && c.tone !== 'web' && c.tone !== 'tool')
  const extra: DecisionChip[] = []
  for (const [role, n] of roleCounts) extra.push({ id: `role:${role}`, text: `role: ${role}${n > 1 ? ` ×${n}` : ''}`, tone: 'role' })
  for (const k of docKinds) extra.push({ id: `doc_kind:${k}`, text: `doc: ${k.replace(/_/g, ' ')}`, tone: 'doc' })
  chips.splice(insertAt < 0 ? chips.length : insertAt, 0, ...extra)
  return chips
}

function tileList(meta: Record<string, unknown> | undefined): Tile[] {
  const raw = meta?.tiles
  if (!Array.isArray(raw)) return []
  return raw.filter((t): t is Tile => !!t && typeof t === 'object' && typeof (t as Tile).kind === 'string')
}

// ── Builder ─────────────────────────────────────────────────────────────────

interface RawRun {
  key: string
  conv: { id: string; title: string }
  user: Message | null
  asst: Message | null
  ts: number
  order: number
}

export function buildRunGraph(
  convs: { id: string; title: string; messages: Message[] }[],
  opts: BuildRunGraphOptions = {},
): RunGraph {
  const nodes: RunNode[] = []
  const edges: RunEdge[] = []
  const nodeById = new Map<string, RunNode>()
  const edgeIds = new Set<string>()
  const runs: RunInfo[] = []

  const addNode = (n: RunNode): RunNode => {
    const existing = nodeById.get(n.id)
    if (existing) return existing
    nodes.push(n)
    nodeById.set(n.id, n)
    return n
  }
  const addEdge = (e: RunEdge) => {
    if (edgeIds.has(e.id) || e.source === e.target) return
    if (!nodeById.has(e.source) || !nodeById.has(e.target)) return
    edgeIds.add(e.id)
    edges.push({ ...e, label: typeof e.label === 'string' ? e.label : String(e.label ?? '') })
  }

  // Pass 1: pair each doc_user question with the doc_run that answered it.
  const raw: RawRun[] = []
  let order = 0
  for (const conv of convs || []) {
    const convInfo = { id: conv.id, title: conv.title || 'Conversation' }
    let pendingUser: Message | null = null
    for (const msg of conv.messages || []) {
      if (!msg) continue
      const kind = msg.meta?.kind
      if (msg.role === 'user' && kind === 'doc_user') {
        if (pendingUser) raw.push({ key: `u-${pendingUser.id}`, conv: convInfo, user: pendingUser, asst: null, ts: pendingUser.timestamp || 0, order: order++ })
        pendingUser = msg
      } else if (msg.role === 'assistant' && kind === 'doc_run') {
        raw.push({ key: msg.id, conv: convInfo, user: pendingUser, asst: msg, ts: pendingUser?.timestamp || msg.timestamp || 0, order: order++ })
        pendingUser = null
      }
    }
    if (pendingUser) raw.push({ key: `u-${pendingUser.id}`, conv: convInfo, user: pendingUser, asst: null, ts: pendingUser.timestamp || 0, order: order++ })
  }
  // Chronological across conversations; stable within one.
  raw.sort((a, b) => a.ts - b.ts || a.order - b.order)

  const keep = Math.max(1, opts.keepExpanded ?? 3)
  const forceId = opts.forceExpandMessageId || null

  const ensureModel = (name: string, run: RawRun, role: string): RunNode => {
    const isLaya = name === 'Laya'
    const node = addNode({
      id: `model:${name}`,
      kind: 'model',
      label: isLaya ? 'Laya' : name,
      data: { modelName: name, roles: [] as string[], useCount: 0, runKeys: [] as string[], isLaya },
      runKey: null,
      column: 3,
    })
    const roles = node.data.roles as string[]
    if (!roles.includes(role)) roles.push(role)
    const runKeys = node.data.runKeys as string[]
    if (!runKeys.includes(run.key)) {
      runKeys.push(run.key)
      node.data.useCount = runKeys.length
    }
    return node
  }

  raw.forEach((run, index) => {
    const { user, asst, key } = run
    const meta = (asst?.meta || {}) as Record<string, unknown>
    const tiles = tileList(asst?.meta)
    const layaTile = tiles.find(t => t.kind === 'laya')
    const decisions = (Array.isArray(layaTile?.decisions) ? layaTile!.decisions : []) as Decision[]
    const intentDecision = decisions.find(d => d?.id === 'intent')
    const answerTile = tiles.find(t => t.kind === 'llm')
    const answerModelName =
      (decisions.find(d => d?.id === 'answer_model')?.value as string | undefined) || answerTile?.model || asst?.model || null
    const questionText = (user?.content || '').trim() || '(no question)'
    const error = meta.error ? String(meta.error) : null
    const cancelled = !!meta.cancelled
    const status: RunInfo['status'] = !asst
      ? 'pending'
      : asst.isStreaming
        ? 'streaming'
        : error
          ? 'error'
          : cancelled
            ? 'cancelled'
            : 'done'
    const forced = !!forceId && (asst?.id === forceId || user?.id === forceId)
    const collapsed =
      !!opts.collapseOlder && index < raw.length - keep && !forced && !(opts.expandedRuns?.has(key) ?? false)

    const info: RunInfo = {
      key,
      index,
      conversationId: run.conv.id,
      conversationTitle: run.conv.title,
      userMessageId: user?.id ?? null,
      assistantMessageId: asst?.id ?? null,
      runId: typeof meta.run_id === 'string' ? meta.run_id : null,
      question: questionText,
      timestamp: user?.timestamp || asst?.timestamp || null,
      status,
      answerModel: answerModelName,
      intent: (intentDecision?.value as string) || (typeof meta.intent === 'string' ? meta.intent : null),
      collapsed,
      nodeIds: [],
      headId: '',
    }
    runs.push(info)

    const answerContent = asst?.content || ''
    const stats = (meta.stats || {}) as Record<string, unknown>
    const answer = asst
      ? {
          model: answerModelName,
          ms: typeof stats.total_ms === 'number' ? stats.total_ms : null,
          status,
          error,
          preview: truncate(answerContent, 400),
        }
      : null

    // Documents (shared): attachments + any doc a tile worked on.
    const docs: { id: string; name: string; role: string | null; pages: unknown; kind: string | null }[] = []
    const seenDocs = new Set<string>()
    const attachments = Array.isArray((user?.meta as any)?.attachments) ? ((user!.meta as any).attachments as any[]) : []
    const carriedOver = !!(user?.meta as any)?.carried_over
    for (const att of attachments) {
      const id = att?.doc_id ? String(att.doc_id) : ''
      if (!id || seenDocs.has(id)) continue
      seenDocs.add(id)
      docs.push({ id, name: String(att.name || `Doc ${id}`), role: att.role && att.role !== 'auto' ? String(att.role) : null, pages: att.pages ?? null, kind: att.kind ? String(att.kind) : null })
    }
    for (const t of tiles) {
      const id = t.doc?.doc_id ? String(t.doc.doc_id) : ''
      if (!id || seenDocs.has(id)) continue
      seenDocs.add(id)
      docs.push({ id, name: String(t.doc?.name || `Doc ${id}`), role: null, pages: null, kind: null })
    }
    // Roles Laya assigned per document.
    const roleByDoc = new Map<string, string>()
    for (const d of decisions) {
      if (d?.id?.startsWith('role-') && d.value) roleByDoc.set(d.id.slice(5), String(d.value))
    }
    const docNodes = docs.map(doc => {
      const n = addNode({
        id: `doc:${doc.id}`,
        kind: 'document',
        label: doc.name,
        data: { docId: doc.id, filename: doc.name, kind: inferDocKind(doc.name), pages: doc.pages, roles: [] as string[], runKeys: [] as string[] },
        runKey: null,
        column: 0,
      })
      const role = doc.role || roleByDoc.get(doc.id) || null
      const roles = n.data.roles as string[]
      if (role && !roles.includes(role)) roles.push(role)
      const rk = n.data.runKeys as string[]
      if (!rk.includes(key)) rk.push(key)
      if (doc.pages != null && n.data.pages == null) n.data.pages = doc.pages
      return { node: n, role }
    })

    // ── Collapsed: a single compact card ──
    if (collapsed) {
      const compact = addNode({
        id: `run:${key}`,
        kind: 'runCompact',
        label: truncate(questionText, 90),
        data: {
          fullText: questionText,
          timestamp: info.timestamp,
          intent: info.intent,
          answerModel: answerModelName,
          status,
          answer,
          decisionCount: decisions.length,
          chips: decisionChips(decisions),
          resultCount: tiles.filter(t => t.kind === 'web' || t.kind === 'planner' || t.kind === 'store' || t.kind === 'query').length,
          conversationTitle: run.conv.title,
        },
        runKey: key,
        column: 1,
      })
      info.nodeIds.push(compact.id)
      info.headId = compact.id
      for (const { node, role } of docNodes) {
        addEdge({ id: `e:${key}:doc:${node.data.docId}`, source: node.id, target: compact.id, label: `about${role ? ` · ${role}` : ''}`, kind: 'about', category: 'document', runKey: key })
      }
      if (answerModelName) {
        const m = ensureModel(answerModelName, run, 'answer')
        addEdge({ id: `e:${key}:answer`, source: compact.id, target: m.id, label: 'answer model', kind: 'answer-model', category: 'model', runKey: key, failed: status === 'error' })
      }
      return
    }

    // ── Expanded ──
    const q = addNode({
      id: `q:${key}`,
      kind: 'question',
      label: truncate(questionText, 120),
      data: {
        fullText: questionText,
        timestamp: info.timestamp,
        answer,
        intent: info.intent,
        conversationTitle: run.conv.title,
        conversationId: run.conv.id,
      },
      runKey: key,
      column: 1,
    })
    info.nodeIds.push(q.id)
    info.headId = q.id
    for (const { node, role } of docNodes) {
      const carried = carriedOver ? ' (carried over)' : ''
      addEdge({ id: `e:${key}:doc:${node.data.docId}`, source: node.id, target: q.id, label: `about${role ? ` · ${role}` : ''}${carried}`, kind: 'about', category: 'document', runKey: key })
    }

    let hub: RunNode = q
    if (decisions.length > 0) {
      const chips = decisionChips(decisions)
      const dec = addNode({
        id: `dec:${key}`,
        kind: 'decisions',
        label: 'Laya decisions',
        data: {
          chips,
          decisions,
          intent: intentDecision?.value ?? null,
          intentConfidence: intentDecision?.confidence ?? null,
          answerModel: answerModelName,
          ms: layaTile?.ms ?? null,
          status: layaTile?.status ?? null,
        },
        runKey: key,
        column: 2,
      })
      info.nodeIds.push(dec.id)
      const src = intentDecision?.source
      const edgeLabel =
        src === 'laya' || !src ? `decided by Laya${pct(intentDecision?.confidence)}` : src === 'rules' ? 'keyword rules' : String(src)
      addEdge({ id: `e:${key}:q-dec`, source: q.id, target: dec.id, label: edgeLabel, kind: 'decide', category: 'decision', runKey: key })
      hub = dec
    }

    // Models (shared)
    if (answerModelName) {
      const m = ensureModel(answerModelName, run, 'answer')
      const note = decisions.find(d => d?.id === 'answer_model')?.note
      addEdge({ id: `e:${key}:answer`, source: hub.id, target: m.id, label: 'answer model', kind: 'answer-model', category: 'model', runKey: key, failed: status === 'error', title: note || undefined })
    }
    if (layaTile) {
      const laya = ensureModel('Laya', run, 'decision')
      addEdge({ id: `e:${key}:laya`, source: hub.id, target: laya.id, label: 'classified by', kind: 'laya-classify', category: 'model', runKey: key })
    }
    for (const d of decisions) {
      if (d?.id?.startsWith('vision_fallback_') && d.value) {
        const m = ensureModel(String(d.value), run, 'vision fallback')
        addEdge({ id: `e:${key}:fallback:${d.id}`, source: hub.id, target: m.id, label: 'fallback', kind: 'vision-fallback', category: 'model', runKey: key })
      }
    }
    for (const t of tiles) {
      if (!t.model || (t.kind !== 'ocr' && t.kind !== 'vision' && t.kind !== 'extract')) continue
      const m = ensureModel(String(t.model), run, t.kind)
      addEdge({ id: `e:${key}:tile-model:${t.id}`, source: hub.id, target: m.id, label: t.kind, kind: `${t.kind}-model`, category: 'model', runKey: key, failed: t.status === 'error' })
    }

    // Results chain: planner → web → profiles / knowledge store
    const webLookup = decisions.find(d => d?.id === 'web_lookup')?.value
    let chainTail: RunNode | null = null
    const plannerTile = tiles.find(t => t.kind === 'planner')
    if (plannerTile) {
      const p = addNode({
        id: `planner:${key}`,
        kind: 'planner',
        label: plannerTile.title || 'Query planner',
        data: { status: plannerTile.status, detail: plannerTile.detail, items: plannerTile.items || [], ms: plannerTile.ms ?? null },
        runKey: key,
        column: 4,
      })
      info.nodeIds.push(p.id)
      addEdge({ id: `e:${key}:planner`, source: hub.id, target: p.id, label: webLookup && webLookup !== 'none' ? `web lookup: ${webLookup}` : 'query planning', kind: 'planner-plan', category: 'web', runKey: key, failed: plannerTile.status === 'error' })
      chainTail = p
    }
    const webTile = tiles.find(t => t.kind === 'web')
    if (webTile) {
      const items = (Array.isArray(webTile.items) ? webTile.items : []) as TileItem[]
      const w = addNode({
        id: `web:${key}`,
        kind: 'web',
        label: webTile.title || 'Web lookup',
        data: {
          status: webTile.status,
          detail: webTile.detail,
          model: webTile.model,
          queries: items.filter(i => i?.kind === 'query').map(i => String(i.label ?? '')),
          results: items
            .filter(i => i?.kind === 'result')
            .map(i => ({ label: String(i.label ?? ''), url: i.url || null, detail: i.detail || null, platform: i.platform || null })),
        },
        runKey: key,
        column: 4,
      })
      info.nodeIds.push(w.id)
      addEdge({
        id: `e:${key}:web`,
        source: (chainTail || hub).id,
        target: w.id,
        label: chainTail ? 'web search' : webLookup && webLookup !== 'none' ? `web lookup: ${webLookup}` : 'web lookup',
        kind: 'web-search',
        category: 'web',
        runKey: key,
        failed: webTile.status === 'error',
      })
      chainTail = w
      let count = 0
      for (const item of items) {
        if (count >= 3) break
        if (item?.kind !== 'result' || !item.url || !isSocialProfileUrl(item.url)) continue
        const platform = resolvePlatform(item.platform, item.url)
        let host = ''
        try {
          host = new URL(item.url).hostname
        } catch {
          host = platform ? SOCIAL_PLATFORMS[platform].domains[0] : ''
        }
        const p = addNode({
          id: `profile:${key}:${count}`,
          kind: 'profile',
          label: truncate(String(item.label ?? ''), 60) || host || 'Profile',
          data: { url: item.url, host, platform, detail: item.detail || null, fullLabel: String(item.label ?? '') },
          runKey: key,
          column: 4,
        })
        info.nodeIds.push(p.id)
        addEdge({ id: `e:${key}:profile:${count}`, source: w.id, target: p.id, label: 'found', kind: 'web-profile', category: 'web', runKey: key })
        count++
      }
    }
    tiles
      .filter(t => t.kind === 'store' || t.kind === 'query')
      .forEach((t, i) => {
        const isQuery = t.kind === 'query'
        const s = addNode({
          id: `store:${key}:${i}`,
          kind: 'store',
          label: t.title || (isQuery ? 'Knowledge store query' : 'Knowledge store'),
          data: {
            mode: isQuery ? 'query' : 'ingest',
            status: t.status,
            detail: t.detail,
            items: (Array.isArray(t.items) ? t.items : []).map(it => String(it?.label ?? '')).filter(Boolean),
            preview: t.output_preview || null,
          },
          runKey: key,
          column: 4,
        })
        info.nodeIds.push(s.id)
        const tool = decisions.find(d => d?.id === 'tool')?.value
        addEdge({
          id: `e:${key}:store:${i}`,
          source: (isQuery ? hub : chainTail || hub).id,
          target: s.id,
          label: isQuery ? (tool ? `tool: ${tool}` : 'knowledge query') : 'remembered',
          kind: isQuery ? 'kg-query' : 'kg-ingest',
          category: 'store',
          runKey: key,
          failed: t.status === 'error',
        })
      })
  })

  return { nodes, edges, runs }
}

// ── Queries ─────────────────────────────────────────────────────────────────

/** Text the search box matches against. */
export function nodeSearchText(node: Pick<RunNode, 'label' | 'data'>): string {
  const d = node.data || {}
  const parts: string[] = [node.label || '']
  for (const k of ['fullText', 'filename', 'modelName', 'url', 'host', 'detail', 'intent', 'answerModel', 'fullLabel']) {
    const v = d[k]
    if (typeof v === 'string') parts.push(v)
  }
  const chips = d.chips as DecisionChip[] | undefined
  if (Array.isArray(chips)) for (const c of chips) parts.push(c.text)
  return parts.join(' \n ').toLowerCase()
}

/** Node ids adjacent to `id` (plus `id` itself) and the connecting edge ids. */
export function neighbourhood(edges: { id: string; source: string; target: string }[], id: string): { nodes: Set<string>; edges: Set<string> } {
  const nodes = new Set<string>([id])
  const edgeSet = new Set<string>()
  for (const e of edges) {
    if (e.source === id || e.target === id) {
      nodes.add(e.source)
      nodes.add(e.target)
      edgeSet.add(e.id)
    }
  }
  return { nodes, edges: edgeSet }
}

/** A run's nodes, its edges and the shared nodes those edges touch. */
export function runNeighbourhood(graph: Pick<RunGraph, 'edges'>, run: RunInfo): { nodes: Set<string>; edges: Set<string> } {
  const nodes = new Set<string>(run.nodeIds)
  const edgeSet = new Set<string>()
  for (const e of graph.edges) {
    if (e.runKey !== run.key) continue
    edgeSet.add(e.id)
    nodes.add(e.source)
    nodes.add(e.target)
  }
  return { nodes, edges: edgeSet }
}

export function findRunForMessage(runs: RunInfo[], messageId: string | null | undefined): RunInfo | null {
  if (!messageId) return null
  return runs.find(r => r.assistantMessageId === messageId || r.userMessageId === messageId) || null
}

/** Stable signature of the graph's structure (node + edge ids) for layout caching. */
export function runGraphSignature(graph: Pick<RunGraph, 'nodes' | 'edges'>): string {
  let s = `${graph.nodes.length}/${graph.edges.length}`
  for (const n of graph.nodes) s += `|${n.id}:${n.kind === 'decisions' ? runNodeSize(n).height : ''}`
  for (const e of graph.edges) s += `|${e.id}`
  return s
}

export function formatRunTime(ts: number | null | undefined): string {
  if (!ts || !Number.isFinite(ts)) return ''
  try {
    const d = new Date(ts)
    const now = new Date()
    const sameDay = d.toDateString() === now.toDateString()
    return sameDay
      ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

// ── Layers: swimlanes ───────────────────────────────────────────────────────

export const LANE_LABEL_WIDTH = 190
export const LANE_GAP = 24
export const LANE_PAD_Y = 16
export const LANE_HEADER_HEIGHT = 34
const LANE_COL_GAP = 56
const LANE_STACK_GAP = 12
const LANE_CONTENT_X = LANE_LABEL_WIDTH + 28

export type LaneColumn = 'document' | 'question' | 'decisions' | 'model' | 'planner' | 'web' | 'result'
export const LANE_COLUMN_TITLES: Record<LaneColumn, string> = {
  document: 'Documents',
  question: 'Question',
  decisions: 'Decisions',
  model: 'Model',
  planner: 'Planner',
  web: 'Web',
  result: 'Results',
}
const LANE_COLUMN_ORDER: LaneColumn[] = ['document', 'question', 'decisions', 'model', 'planner', 'web', 'result']

export interface LaneNode {
  id: string
  /** The graph node this renders (for refs: the shared document / model). */
  refOf: string
  isRef: boolean
  kind: RunNodeKind
  laneKey: string
  column: LaneColumn
  x: number
  y: number
  width: number
  height: number
}

export interface LaneEdge {
  id: string
  /** Graph edge id this lane edge draws. */
  edgeId: string
  source: string
  target: string
  laneKey: string
}

export interface Lane {
  key: string
  run: RunInfo
  y: number
  height: number
}

export interface LaneLayout {
  nodes: LaneNode[]
  edges: LaneEdge[]
  lanes: Lane[]
  columns: { column: LaneColumn; x: number; width: number; title: string }[]
  width: number
  height: number
  /** y where the first lane starts (below the column header row). */
  top: number
}

function laneColumnOf(node: RunNode): LaneColumn {
  switch (node.kind) {
    case 'document':
      return 'document'
    case 'question':
    case 'runCompact':
      return 'question'
    case 'decisions':
      return 'decisions'
    case 'model':
      return 'model'
    case 'planner':
      return 'planner'
    case 'web':
      return 'web'
    default:
      return 'result'
  }
}

/**
 * Swimlane layout: one lane per run (latest first). Shared documents / models
 * are repeated in every lane as reference chips (`<id>@<laneKey>`), so every
 * edge stays inside its lane. Within a lane the primary chain (first doc →
 * question → decisions → answer model → first result) shares one row, so its
 * edges are straight; everything else stacks below in its own column.
 */
export function layoutLanes(graph: RunGraph): LaneLayout {
  const nodeById = new Map(graph.nodes.map(n => [n.id, n]))
  const runsLatestFirst = [...graph.runs].sort((a, b) => b.index - a.index)

  interface Pending {
    laneKey: string
    items: { id: string; refOf: string; isRef: boolean; kind: RunNodeKind; column: LaneColumn; width: number; height: number; primary: boolean }[]
    edges: LaneEdge[]
  }
  const pendings: Pending[] = []
  const usedColumns = new Set<LaneColumn>()

  for (const run of runsLatestFirst) {
    const laneEdges = graph.edges.filter(e => e.runKey === run.key)
    const items: Pending['items'] = []
    const localId = (id: string) => {
      const n = nodeById.get(id)
      return n && n.runKey === null ? `${id}@${run.key}` : id
    }
    const add = (id: string) => {
      const lid = localId(id)
      if (items.some(i => i.id === lid)) return
      const n = nodeById.get(id)
      if (!n) return
      const isRef = n.runKey === null
      const size = isRef ? REF_CHIP_SIZE : runNodeSize(n)
      items.push({ id: lid, refOf: id, isRef, kind: n.kind, column: laneColumnOf(n), width: size.width, height: size.height, primary: false })
    }
    for (const id of run.nodeIds) add(id)
    for (const e of laneEdges) {
      add(e.source)
      add(e.target)
    }
    // Lane-local edge endpoints. The first result hangs off the answer model
    // (reads left→right and keeps the edge in adjacent columns); the graph edge
    // id is kept so focus / labels still map to the real relation.
    const answerModelId = run.answerModel ? `model:${run.answerModel}` : null
    const answerRef = answerModelId && items.some(i => i.refOf === answerModelId) ? `${answerModelId}@${run.key}` : null
    const edgesOut: LaneEdge[] = []
    for (const e of laneEdges) {
      let source = localId(e.source)
      const target = localId(e.target)
      const src = nodeById.get(e.source)
      const tgt = nodeById.get(e.target)
      if (answerRef && tgt && tgt.column === 4 && src && (src.kind === 'decisions' || src.kind === 'question')) source = answerRef
      edgesOut.push({ id: `${e.id}@lane`, edgeId: e.id, source, target, laneKey: run.key })
    }
    // Primary row: the first node of each column along the main chain.
    const pick = (pred: (i: Pending['items'][number]) => boolean) => items.find(pred)
    const primaries = [
      pick(i => i.column === 'document'),
      pick(i => i.column === 'question'),
      pick(i => i.column === 'decisions'),
      (answerRef && pick(i => i.id === answerRef)) || pick(i => i.column === 'model'),
      pick(i => i.column === 'planner'),
      pick(i => i.column === 'web'),
      pick(i => i.column === 'result'),
    ]
    for (const p of primaries) if (p) p.primary = true
    for (const i of items) usedColumns.add(i.column)
    pendings.push({ laneKey: run.key, items, edges: edgesOut })
  }

  // Column x positions (only columns some lane uses).
  const columns: LaneLayout['columns'] = []
  let x = LANE_CONTENT_X
  for (const col of LANE_COLUMN_ORDER) {
    if (!usedColumns.has(col)) continue
    let width = 0
    for (const p of pendings) for (const i of p.items) if (i.column === col) width = Math.max(width, i.width)
    columns.push({ column: col, x, width, title: LANE_COLUMN_TITLES[col] })
    x += width + LANE_COL_GAP
  }
  const totalWidth = x - LANE_COL_GAP + 24

  const nodes: LaneNode[] = []
  const lanes: Lane[] = []
  const edges: LaneEdge[] = []
  const top = LANE_HEADER_HEIGHT + 12
  let laneY = top
  const runByKey = new Map(graph.runs.map(r => [r.key, r]))
  for (const p of pendings) {
    const primaryH = Math.max(0, ...p.items.filter(i => i.primary).map(i => i.height))
    const rowCenter = laneY + LANE_PAD_Y + primaryH / 2
    let bottom = laneY + LANE_PAD_Y + primaryH
    for (const col of columns) {
      const inCol = p.items.filter(i => i.column === col.column)
      inCol.sort((a, b) => Number(b.primary) - Number(a.primary))
      let y = laneY + LANE_PAD_Y
      for (const item of inCol) {
        const nx = col.x + (col.width - item.width) / 2
        let ny: number
        if (item.primary) {
          ny = rowCenter - item.height / 2
          y = ny + item.height + LANE_STACK_GAP
        } else {
          ny = y
          y += item.height + LANE_STACK_GAP
        }
        bottom = Math.max(bottom, ny + item.height)
        nodes.push({ id: item.id, refOf: item.refOf, isRef: item.isRef, kind: item.kind, laneKey: p.laneKey, column: item.column, x: nx, y: ny, width: item.width, height: item.height })
      }
    }
    const height = Math.max(bottom - laneY + LANE_PAD_Y, 72)
    lanes.push({ key: p.laneKey, run: runByKey.get(p.laneKey)!, y: laneY, height })
    edges.push(...p.edges)
    laneY += height + LANE_GAP
  }
  return { nodes, edges, lanes, columns, width: totalWidth, height: laneY - LANE_GAP, top }
}
