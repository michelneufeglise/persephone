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
}
export interface StreamDone {
  stats?: { eval_count?: number; tok_per_s?: number; total_duration_ms?: number }
  used_ocr?: string[]
  sources?: string[]
  chunks_used?: number
}
export interface StreamHandlers {
  onPhase?: (p: StreamPhase) => void
  onThinking?: (delta: string) => void
  onContent?: (delta: string) => void
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
  return (delta: string) => {
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
    if (done) break
    buf += dec.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const payload = line.slice(6).trim()
      if (payload === '[DONE]') return
      try {
        const ev = JSON.parse(payload)
        if (ev.error) { h.onError?.(ev.error); continue }
        if (ev.done) { h.onDone?.(ev as StreamDone); continue }
        if (ev.phase) { h.onPhase?.(ev as StreamPhase); continue }
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
