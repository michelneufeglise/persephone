import type { Message } from '@/types'

// ── Types ──────────────────────────────────────────────────────────────────

export interface Decision {
  id: string
  label: string
  value: string
  confidence: number | null
  source: 'laya' | 'rules' | 'probe' | 'config' | 'user'
  note: string | null
  probabilities: Record<string, number> | null
}

export interface TileItem {
  kind: 'query' | 'result' | 'note'
  label: string
  /** Table-query tiles: result columns (on the query item) and one result row (on result items). */
  columns?: string[]
  row?: (string | number | boolean | null)[]
  url?: string | null
  detail?: string | null
  /** Social platform of a result ("linkedin" | "facebook" | "instagram" | "x"), null for web pages. */
  platform?: string | null
}

/** One row of the signature engine's per-feature breakdown. */
export interface SignatureFeature {
  feature: string
  label: string
  /** Mean distance of the questioned signature to the references. */
  questioned_distance: number
  /** Typical distance between the reference signatures (leave-one-out mean). */
  reference_spread: number
  reference_sd?: number
  /** (questioned − spread) / sd — how unusual the questioned signature is for this feature. */
  z: number
  weight?: number
  verdict: 'consistent' | 'borderline' | 'different'
}

export interface SignatureCrop {
  url: string
  doc?: string | null
  bbox?: number[] | null
}

/** `data` of the "signature-check" tile (see server/doc_signature.py). */
export interface SignatureTileData {
  type: 'signature'
  score: number
  band: 'consistent' | 'inconclusive' | 'inconsistent'
  band_label: string
  combined_z?: number | null
  n_references: number
  breakdown: SignatureFeature[]
  reasons: string[]
  warnings: string[]
  model: string | null
  assessment: string | null
  questioned: SignatureCrop | null
  references: SignatureCrop[]
  candidates?: { count: number; picked: number; picked_by: 'model' | 'heuristic'; urls?: string[]; sheet?: string | null } | null
  engine?: string | null
  questioned_doc?: string | null
  reference_docs?: string[]
  caveat?: string | null
}

export interface Tile {
  id: string
  kind: 'laya' | 'extract' | 'ocr' | 'llm' | 'vision' | 'planner' | 'web' | 'query' | 'store' | 'table' | 'signature'
  title: string
  status: 'pending' | 'running' | 'done' | 'skipped' | 'error'
  model: string | null
  model_info: {
    name?: string
    family?: string
    parameter_size?: string
    quantization?: string
    size_gb?: number
    is_vision?: boolean
    [k: string]: unknown
  } | null
  detail: string
  decisions: Decision[]
  started_ms: number | null
  ms: number | null
  output_preview: string | null
  doc: { doc_id: string; name: string } | null
  items?: TileItem[]
  /** Structured payload (signature-check tile: SignatureTileData). */
  data?: SignatureTileData | Record<string, unknown> | null
}

/** The signature-check payload of a tile, when it has one. */
export function signatureData(tile: Pick<Tile, 'data'> | null | undefined): SignatureTileData | null {
  const d = tile?.data as Record<string, unknown> | null | undefined
  return d && d.type === 'signature' && typeof d.score === 'number' ? (d as unknown as SignatureTileData) : null
}

// ── Knowledge Graph Types ──────────────────────────────────────────────────

export interface KGEntity {
  id: string
  type: 'person' | 'organization' | 'role' | 'document' | 'profile' | 'location'
  name: string
  props: Record<string, unknown>
  mention_count: number
}

export interface KGRelation {
  id: string
  src: string
  dst: string
  type:
    | 'has_role'
    | 'works_at'
    | 'owns'
    | 'mentioned_in'
    | 'candidate_profile'
    | 'likely_profile'
    | 'located_in'
    | 'signed'
    | 'signature_specimen'
    | 'verified_against'
  confidence: number
  source: 'doc_agent' | 'web_lookup' | 'signature_engine'
  props: Record<string, unknown>
}

export interface KGDocument {
  doc_id: string
  name: string
  chunk_count: number
  mention_count: number
}

export interface KGStats {
  entities: number
  relations: number
  mentions: number
  documents: number
  chunks: number
}

export interface KGGraph {
  entities: KGEntity[]
  relations: KGRelation[]
  documents: KGDocument[]
  stats: KGStats
}

export type DocAgentEvent =
  | { meta: { conversation_id: string; run_id: string } }
  | { tile: Tile }
  | { thinking: string }
  | { content: string }
  | { done: true; stats?: Record<string, unknown> }
  | { error: string }

export interface DocConversationSummary {
  id: string
  title: string
  model: string
  createdAt: number
  updatedAt: number
  messageCount: number
}

// ── SSE Streaming ──────────────────────────────────────────────────────────

/**
 * Stream document agent responses as an async generator.
 * Handles SSE parsing with proper buffering across chunk boundaries.
 * Errors are surfaced as {error} events; HTTP errors throw immediately.
 */
export async function* streamDocAgent(
  body: {
    conversation_id?: string | null
    message: string
    attachments: { doc_id: string; role: 'auto' | 'subject' | 'reference' }[]
    model_override?: string | null
    user_message_id?: string
    assistant_message_id?: string
  },
  signal?: AbortSignal,
): AsyncGenerator<DocAgentEvent> {
  let res: Response
  try {
    res = await fetch('/api/idp/agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
  } catch (e) {
    // A user-initiated abort (Stop / switching conversation) is not an error.
    if ((e instanceof Error || e instanceof DOMException) && e.name === 'AbortError') return
    if (signal?.aborted) return
    yield { error: e instanceof Error ? e.message : 'Network error' }
    return
  }

  if (!res.ok || !res.body) {
    let msg = `Stream failed (HTTP ${res.status})`
    try {
      const b = await res.json()
      if (typeof b?.detail === 'string') msg = b.detail
    } catch {
      /* not json */
    }
    yield { error: msg }
    return
  }

  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buf += dec.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() ?? ''

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const payload = line.slice(6).trim()
        if (payload === '[DONE]') return

        try {
          const ev = JSON.parse(payload) as Record<string, unknown>

          // Surface errors immediately
          if (typeof ev.error === 'string') {
            yield { error: ev.error }
            continue
          }

          // Pass through all known event types (type-safe via explicit casts)
          if (ev.meta && typeof ev.meta === 'object') {
            yield {
              meta: ev.meta as {
                conversation_id: string
                run_id: string
              },
            }
            continue
          }
          if (ev.tile && typeof ev.tile === 'object') {
            yield { tile: ev.tile as Tile }
            continue
          }
          if (typeof ev.thinking === 'string') {
            yield { thinking: ev.thinking }
            continue
          }
          if (typeof ev.content === 'string') {
            yield { content: ev.content }
            continue
          }
          if (ev.done === true) {
            yield { done: true, stats: ev.stats as Record<string, unknown> }
            continue
          }
        } catch {
          /* skip malformed JSON */
        }
      }
    }
  } catch (e) {
    if (e instanceof Error && e.name !== 'AbortError') {
      yield { error: e.message }
    }
  }
}

// ── Conversation Management ────────────────────────────────────────────────

/**
 * Fetch and filter document conversations (ids starting with 'dconv-'), newest first.
 */
export async function listDocConversations(): Promise<DocConversationSummary[]> {
  try {
    const res = await fetch('/api/memory/conversations')
    if (!res.ok) return []
    // GET /api/memory/conversations returns a bare array (db.list_conversations).
    const data = (await res.json()) as unknown
    const convs = (Array.isArray(data) ? data : ((data as Record<string, unknown>)?.conversations ?? [])) as Array<{
      id: string
      title: string
      model: string
      createdAt: number
      updatedAt: number
      messageCount: number
    }>
    return convs.filter(c => c.id.startsWith('dconv-')).sort((a, b) => b.updatedAt - a.updatedAt)
  } catch {
    return []
  }
}

/**
 * Load messages from a document conversation.
 * Maps DB message structure to frontend Message type.
 */
export async function loadDocConversation(id: string): Promise<Message[]> {
  try {
    const res = await fetch(`/api/memory/conversations/${encodeURIComponent(id)}`)
    if (!res.ok) return []
    const data = (await res.json()) as Record<string, unknown>
    const msgs = (data.messages ?? []) as Array<{
      id: string
      role: 'user' | 'assistant' | 'system'
      content: string
      thinkingContent?: string
      model?: string
      timestamp: number
      meta?: Record<string, unknown>
    }>
    return msgs.map(m => ({
      id: m.id,
      role: m.role,
      content: m.content,
      thinkingContent: m.thinkingContent,
      model: m.model,
      timestamp: m.timestamp,
      meta: m.meta,
    }))
  } catch {
    return []
  }
}

/**
 * Delete a document conversation.
 */
export async function deleteDocConversation(id: string): Promise<void> {
  try {
    await fetch(`/api/memory/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' })
  } catch {
    /* ignore */
  }
}

/**
 * Reset the knowledge store (DELETE /api/kg): wipes every entity, relation and
 * mention the doc agent has learned. Throws on failure so the caller can surface it.
 */
export async function resetKnowledgeStore(): Promise<void> {
  const res = await fetch('/api/kg', { method: 'DELETE' })
  if (!res.ok) {
    let msg = `Reset failed (HTTP ${res.status})`
    try {
      const b = await res.json()
      if (typeof b?.detail === 'string') msg = b.detail
    } catch {
      /* not json */
    }
    throw new Error(msg)
  }
}

/**
 * Fetch knowledge graph data for a given scope.
 * Returns empty graph on error to ensure graceful fallback.
 */
export async function fetchKnowledgeGraph(
  scope: 'all' | 'conversation',
  conversationId?: string,
): Promise<KGGraph> {
  const defaultGraph: KGGraph = {
    entities: [],
    relations: [],
    documents: [],
    stats: { entities: 0, relations: 0, mentions: 0, documents: 0, chunks: 0 },
  }

  try {
    const params = new URLSearchParams({
      scope,
      ...(conversationId && scope === 'conversation' && { conversation_id: conversationId }),
    })
    const res = await fetch(`/api/kg/graph?${params}`)
    if (!res.ok) return defaultGraph
    const data = (await res.json()) as unknown

    // Validate shape
    if (
      typeof data === 'object' &&
      data !== null &&
      'entities' in data &&
      'relations' in data &&
      'documents' in data &&
      'stats' in data &&
      Array.isArray((data as any).entities) &&
      Array.isArray((data as any).relations) &&
      Array.isArray((data as any).documents)
    ) {
      return data as KGGraph
    }
    return defaultGraph
  } catch {
    return defaultGraph
  }
}

export interface KGEvidenceDoc {
  entity_id: string
  doc_id: string
  filename: string
  kind: string
  signature_card: boolean
}

/** GET /api/kg/relation-evidence — provenance of one relation (evidence panel). */
export interface KGRelationEvidence {
  relation: {
    id: string
    src: string
    dst: string
    type: string
    confidence: number | null
    source: string
    props: Record<string, unknown>
    run_id: string | null
    conversation_id: string | null
    created_at: number | null
  }
  src_entity: { id: string; type: string; name: string }
  dst_entity: { id: string; type: string; name: string }
  source_doc: KGEvidenceDoc | null
  reference_doc: KGEvidenceDoc | null
  conversation: { id: string; title: string | null } | null
  snippet: string | null
  snippet_source: 'document' | 'mention' | 'profile' | null
  highlights: string[]
  /** likely_profile / candidate_profile: the web profile itself is the evidence. */
  profile?: {
    title: string
    url: string | null
    host: string | null
    platform: string
    confidence: number | null
    verified_at: string | null
    snippet: string | null
  } | null
  signature: {
    score: number | null
    band: string | null
    n_references: number | null
    verified_at: string | null
    person: string | null
    questioned: string | null
    references: string[]
    crops_source: 'stored' | 'derived' | null
  } | null
}

export async function fetchRelationEvidence(relationId: string, signal?: AbortSignal): Promise<KGRelationEvidence> {
  const res = await fetch(`/api/kg/relation-evidence?${new URLSearchParams({ id: relationId })}`, { signal })
  if (!res.ok) throw new Error(res.status === 404 ? 'This relation is no longer in the knowledge store.' : `Could not load evidence (HTTP ${res.status})`)
  const data = (await res.json()) as KGRelationEvidence
  if (!data || typeof data !== 'object' || !data.relation) throw new Error('Unexpected evidence response')
  return data
}

// ── Helpers ────────────────────────────────────────────────────────────────

/**
 * Infer document kind from filename.
 */
export function docKindFromName(name: string): 'pdf' | 'image' | 'email' | 'docx' | 'sheet' | 'text' | 'other' {
  const lower = name.toLowerCase()
  if (lower.endsWith('.pdf')) return 'pdf'
  if (/\.(png|jpg|jpeg|gif|webp|svg|heic|heif|tif|tiff)$/i.test(lower)) return 'image'
  if (/\.(eml|msg)$/i.test(lower)) return 'email'
  if (/\.(docx?|odt|rtf)$/i.test(lower)) return 'docx'
  if (/\.(xlsx|xlsm|xls|ods|csv|tsv)$/i.test(lower)) return 'sheet'
  if (/\.(txt|md)$/i.test(lower)) return 'text'
  return 'other'
}

// ── Spreadsheets ───────────────────────────────────────────────────────────

/** Upload `accept` list for spreadsheets (kept in one place for all inputs). */
export const SHEET_ACCEPT = '.xlsx,.xlsm,.xls,.ods,.csv,.tsv'

export type SheetCell = string | number | boolean | null

export interface SheetListEntry {
  name: string
  rows: number
  cols: number
  hidden: boolean
}

export interface SheetPreview {
  sheets: SheetListEntry[]
  sheet: string | null
  columns: string[]
  /** Per column: number | date | bool | text | mixed | empty */
  dtypes: string[]
  rows: SheetCell[][]
  offset: number
  total: number
  formula_columns?: string[]
  /** Hidden sheets in the workbook: counted only, never listed or served. */
  hidden_sheets?: number
}

/** Rows of one sheet of a spreadsheet document (GET /api/idp/documents/{id}/sheets). */
export async function fetchSheetPreview(
  docId: string,
  sheet?: string | null,
  offset = 0,
  limit = 200,
  signal?: AbortSignal,
): Promise<SheetPreview> {
  const params = new URLSearchParams({ offset: String(offset), limit: String(limit) })
  if (sheet) params.set('sheet', sheet)
  const res = await fetch(`/api/idp/documents/${encodeURIComponent(docId)}/sheets?${params}`, { signal })
  if (!res.ok) {
    let msg = `Could not load the sheet (HTTP ${res.status})`
    try {
      const b = await res.json()
      if (typeof b?.detail === 'string') msg = b.detail
    } catch {
      /* not json */
    }
    throw new Error(msg)
  }
  return (await res.json()) as SheetPreview
}

export interface SheetSummary {
  /** File format label, e.g. "XLSX" */
  format: string
  /** Visible sheets */
  sheets: number
  hidden: number
  /** Rows across visible sheets */
  rows: number
}

/** Sheet counts from a document's meta (list format; legacy {name: {rows, cols}} maps too). */
export function sheetSummary(doc: { filename: string; meta?: Record<string, unknown> | null }): SheetSummary | null {
  if (docKindFromName(doc.filename) !== 'sheet') return null
  const raw = (doc.meta ?? {})['sheets']
  let list: { rows: number; hidden: boolean }[] = []
  if (Array.isArray(raw)) {
    list = raw.map((s: any) => ({ rows: Number(s?.rows) || 0, hidden: !!s?.hidden }))
  } else if (raw && typeof raw === 'object') {
    list = Object.values(raw as Record<string, any>).map(s => ({ rows: Number(s?.rows) || 0, hidden: false }))
  }
  const visible = list.filter(s => !s.hidden)
  const format = (doc.filename.split('.').pop() || 'sheet').toUpperCase()
  return {
    format,
    sheets: visible.length,
    hidden: list.length - visible.length,
    rows: visible.reduce((n, s) => n + s.rows, 0),
  }
}

/** "3 sheets · 1,240 rows" (or "1 sheet" when the row count is unknown). */
export function sheetSummaryLabel(s: SheetSummary): string {
  const parts = [`${s.sheets} sheet${s.sheets === 1 ? '' : 's'}`]
  if (s.rows > 0) parts.push(`${s.rows.toLocaleString()} row${s.rows === 1 ? '' : 's'}`)
  return parts.join(' · ')
}
