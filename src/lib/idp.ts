import type { IDPDocument } from '@/types'

export async function listDocuments(): Promise<IDPDocument[]> {
  const r = await fetch('/api/idp/documents')
  if (!r.ok) return []
  return (await r.json()).documents ?? []
}

export async function getDocument(id: string): Promise<IDPDocument | null> {
  const r = await fetch(`/api/idp/documents/${id}`)
  if (!r.ok) return null
  return await r.json()
}

export async function uploadDocument(file: File): Promise<IDPDocument | null> {
  const form = new FormData()
  form.append('file', file)
  const r = await fetch('/api/idp/upload', { method: 'POST', body: form })
  if (!r.ok) throw new Error(`Upload failed: ${r.status}`)
  return await r.json()
}

export async function deleteDocument(id: string): Promise<boolean> {
  const r = await fetch(`/api/idp/documents/${id}`, { method: 'DELETE' })
  return r.ok
}

async function callIdp<T>(action: string, docId: string, options: Record<string, unknown> = {}): Promise<T> {
  const r = await fetch(`/api/idp/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ doc_id: docId, options }),
  })
  if (!r.ok) {
    // FastAPI HTTPException returns { detail: "message" } as JSON
    let msg = `${action} failed (HTTP ${r.status})`
    try {
      const body = await r.json()
      if (typeof body?.detail === 'string') msg = body.detail
    } catch { /* not JSON */ }
    throw new Error(msg)
  }
  return await r.json()
}

export interface IDPResult       { text: string;          model: string }
export interface IDPEntities     { entities: Record<string, string[]>; model: string }
export interface IDPClassify     { classification: { type?: string; language?: string; confidence?: number; topics?: string[] }; model: string }
export interface IDPTables       { tables: { title?: string; headers?: string[]; rows?: string[][] }[]; model: string }
export interface IDPMultiResult  { text: string; model: string; used_ocr: string[]; sources: string[]; chunks_used: number }

export async function multiQa(docIds: string[], question: string, autoOcr = true): Promise<IDPMultiResult> {
  const r = await fetch('/api/idp/multi-qa', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ doc_ids: docIds, question, auto_ocr: autoOcr }),
  })
  if (!r.ok) {
    let msg = `multi-qa failed (HTTP ${r.status})`
    try { const b = await r.json(); if (typeof b?.detail === 'string') msg = b.detail } catch { /* not json */ }
    throw new Error(msg)
  }
  return await r.json()
}

export const idp = {
  ocr:       (id: string)                              => callIdp<IDPResult>('ocr',       id),
  summarize: (id: string, style = 'brief')             => callIdp<IDPResult>('summarize', id, { style }),
  qa:        (id: string, question: string)            => callIdp<IDPResult>('qa',        id, { question }),
  tables:    (id: string)                              => callIdp<IDPTables>('tables',    id),
  entities:  (id: string)                              => callIdp<IDPEntities>('entities', id),
  classify:  (id: string)                              => callIdp<IDPClassify>('classify', id),
  translate: (id: string, target: string)              => callIdp<IDPResult>('translate', id, { target }),
  redact:    (id: string, categories: string[])        => callIdp<IDPResult>('redact',    id, { categories }),
  humanize:  (id: string, tone: string, intensity: string) =>
    callIdp<IDPResult>('humanize', id, { tone, intensity }),
}

export function exportUrl(id: string, fmt: string): string {
  return `/api/idp/export/${fmt}`
}

export async function exportDoc(id: string, fmt: 'md' | 'txt' | 'pdf' | 'json' | 'xlsx' | 'csv'): Promise<Blob | null> {
  const r = await fetch(`/api/idp/export/${fmt}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ doc_id: id, options: {} }),
  })
  if (!r.ok) return null
  return await r.blob()
}

export function pageImageUrl(docId: string, pageNum: number): string {
  return `/api/idp/documents/${docId}/page/${pageNum}`
}

// ── Streaming document ops (live tokens + thinking + tok/s) ──────────────────
export interface StreamPhase {
  phase: string
  detail?: string
  model?: string
  chunks_used?: number
  used_ocr?: string[]
  sources?: string[]
  /** Block-wise writing ops (humanize / translate / redact): current block k of N. */
  i?: number
  of?: number
  language?: string
}
/** A block the writing pipeline kept in its original form (quality guard). */
export interface StreamFlag {
  i: number
  idx?: number
  issues: string[]
  preview?: string
}
export interface StreamDone {
  stats?: { eval_count?: number; tok_per_s?: number; total_duration_ms?: number }
  used_ocr?: string[]
  sources?: string[]
  chunks_used?: number
  /** Humanize: id of the cached result (used for the .docx download). */
  run_id?: string
  flagged?: StreamFlag[]
  blocks?: number
  /** Humanize: the source is a .docx, so a Word download is available. */
  docx?: boolean
  language?: string
}
export interface StreamHandlers {
  onPhase?: (p: StreamPhase) => void
  onThinking?: (delta: string) => void
  onContent?: (delta: string) => void
  /** Live, not-yet-validated tokens of the block being written. */
  onDraft?: (delta: string) => void
  onDraftReset?: () => void
  onFlag?: (f: StreamFlag) => void
  onDone?: (d: StreamDone) => void
  onError?: (msg: string) => void
}

// Routes inline <think>…</think> found in CONTENT deltas to the thinking
// channel (for models that don't use Ollama's separate `thinking` field).
// Buffers across chunk boundaries so a tag split over two deltas still works.
function makeThinkSplitter(onThinking: (s: string) => void, onContent: (s: string) => void) {
  let inThink = false
  let buf = ''
  const OPEN = '<think>'
  const CLOSE = '</think>'
  const push = (delta: string) => {
    buf += delta
    // Emit as much as is unambiguous; keep a tail that might be a partial tag.
    while (buf.length) {
      const tag = inThink ? CLOSE : OPEN
      const idx = buf.indexOf(tag)
      if (idx === -1) {
        // Hold back up to (tag.length-1) chars in case a tag is split.
        const safe = buf.length - (tag.length - 1)
        if (safe > 0) {
          const out = buf.slice(0, safe)
          inThink ? onThinking(out) : onContent(out)
          buf = buf.slice(safe)
        }
        break
      }
      const out = buf.slice(0, idx)
      if (out) (inThink ? onThinking(out) : onContent(out))
      buf = buf.slice(idx + tag.length)
      inThink = !inThink
    }
  }
  // End of stream: emit the held-back tail (otherwise the last few
  // characters of every answer were never shown).
  const flush = () => {
    if (buf) { inThink ? onThinking(buf) : onContent(buf); buf = '' }
  }
  return Object.assign(push, { flush })
}

async function consumeSSE(res: Response, h: StreamHandlers): Promise<void> {
  if (!res.ok || !res.body) {
    let msg = `stream failed (HTTP ${res.status})`
    try { const b = await res.json(); if (typeof b?.detail === 'string') msg = b.detail } catch { /* not json */ }
    h.onError?.(msg); return
  }
  const split = makeThinkSplitter(
    d => h.onThinking?.(d),
    d => h.onContent?.(d),
  )
  const reader = res.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) { split.flush(); break }
    buf += dec.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const payload = line.slice(6).trim()
      if (payload === '[DONE]') { split.flush(); return }
      try {
        const ev = JSON.parse(payload)
        if (ev.error) { split.flush(); h.onError?.(ev.error); continue }
        if (ev.done) { split.flush(); h.onDone?.(ev as StreamDone); continue }
        if (ev.phase) { h.onPhase?.(ev as StreamPhase); continue }
        if (typeof ev.draft === 'string') { h.onDraft?.(ev.draft); continue }
        if (ev.draft_reset) { h.onDraftReset?.(); continue }
        if (ev.flag) { h.onFlag?.(ev.flag as StreamFlag); continue }
        if (typeof ev.thinking === 'string') { h.onThinking?.(ev.thinking); continue }
        if (typeof ev.content === 'string') { split(ev.content); continue }
      } catch { /* skip malformed */ }
    }
  }
}

export async function streamIdp(
  op: string, docId: string, options: Record<string, unknown>,
  h: StreamHandlers, signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`/api/idp/stream/${op}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ doc_id: docId, options }),
    signal,
  })
  await consumeSSE(res, h)
}

/** Download the humanized text written back into the original .docx layout. */
export async function downloadHumanizedDocx(docId: string, runId: string): Promise<{ blob: Blob; filename: string }> {
  const r = await fetch('/api/idp/humanize/docx', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ doc_id: docId, run_id: runId }),
  })
  if (!r.ok) {
    let msg = `Download failed (HTTP ${r.status})`
    try { const b = await r.json(); if (typeof b?.detail === 'string') msg = b.detail } catch { /* not json */ }
    throw new Error(msg)
  }
  const cd = r.headers.get('Content-Disposition') || ''
  let filename = 'humanized.docx'
  const star = /filename\*=UTF-8''([^;]+)/i.exec(cd)
  const plain = /filename="([^"]+)"/i.exec(cd)
  if (star) { try { filename = decodeURIComponent(star[1]) } catch { /* keep default */ } }
  else if (plain) filename = plain[1]
  return { blob: await r.blob(), filename }
}

export async function streamMultiQa(
  docIds: string[], question: string, autoOcr: boolean,
  h: StreamHandlers, signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/idp/stream/multi-qa', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ doc_ids: docIds, question, auto_ocr: autoOcr }),
    signal,
  })
  await consumeSSE(res, h)
}

// ── Routing & text ingest ──────────────────────────────────────────
export interface RouteTraceStep {
  node: string
  status: 'ok' | 'skipped' | 'fallback' | 'error'
  ms: number
  detail: string
  output?: unknown
}

export interface RouteDecision {
  kind: string | null
  confidence: number | null
  complexity: number | null
  probabilities: Record<string, number>
  source: 'laya' | 'rules'
}

export interface RouteProbe {
  mime: string
  pages: number
  chars: number
  has_text_layer: boolean
  has_page_images: boolean
  table_density: number
  is_email: boolean
}

export interface RouteResult {
  doc_id: string
  category: 'ocr' | 'docs' | 'handwriting' | 'tables' | 'multidoc' | 'text'
  model: string | null
  reason: string
  overridden: boolean
  decision: RouteDecision
  probe: RouteProbe
  trace: RouteTraceStep[]
}

export interface LayaStatus {
  available: boolean
  loaded: boolean
  device?: string | null
  /** The `laya` Python package is importable (weights may still be missing). */
  package_installed?: boolean
  /** A one-time download (started from the wizard / Settings) is running. */
  downloading?: boolean
  /** Last download error, if the most recent attempt failed. */
  error?: string | null
  /** Bytes received so far while downloading (HF cache, incl. partial files). */
  downloaded_bytes?: number | null
  /** Bytes on disk when installed, else the approximate download size. */
  size_bytes?: number | null
  size_is_estimate?: boolean
  /** Device Laya runs (or would run) on: 'mps' | 'cpu'. */
  target_device?: string | null
  params?: string
  repo_id?: string
}

export interface LayaDownloadResult extends LayaStatus {
  state: 'done' | 'running' | 'started' | 'unavailable'
}

export async function ingestText(text: string, title?: string, kind?: 'email' | 'text'): Promise<IDPDocument | null> {
  const r = await fetch('/api/idp/ingest-text', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, title, kind }),
  })
  if (!r.ok) throw new Error(`Ingest failed: ${r.status}`)
  return await r.json()
}

export async function routeDocument(docId: string, opts?: { force?: boolean; overrideModel?: string | null }): Promise<RouteResult> {
  const r = await fetch('/api/idp/route', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ doc_id: docId, force: opts?.force ?? false, override_model: opts?.overrideModel }),
  })
  if (!r.ok) {
    let msg = `Route failed (HTTP ${r.status})`
    try {
      const body = await r.json()
      if (typeof body?.detail === 'string') msg = body.detail
    } catch { /* not JSON */ }
    throw new Error(msg)
  }
  return await r.json()
}

export async function layaStatus(): Promise<LayaStatus> {
  const r = await fetch('/api/idp/route/status')
  if (!r.ok) return { available: false, loaded: false }
  return await r.json()
}

/** Start the one-time Laya download from Hugging Face (idempotent; only ever
 *  called from an explicit user click). Poll layaStatus() for progress. */
export async function startLayaDownload(): Promise<LayaDownloadResult> {
  const r = await fetch('/api/idp/route/download', { method: 'POST' })
  if (!r.ok) throw new Error(`Download request failed (HTTP ${r.status})`)
  return await r.json()
}
