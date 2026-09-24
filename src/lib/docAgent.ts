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

export interface Tile {
  id: string
  kind: 'laya' | 'extract' | 'ocr' | 'llm' | 'vision'
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

// ── Helpers ────────────────────────────────────────────────────────────────

/**
 * Infer document kind from filename.
 */
export function docKindFromName(name: string): 'pdf' | 'image' | 'email' | 'docx' | 'sheet' | 'text' | 'other' {
  const lower = name.toLowerCase()
  if (lower.endsWith('.pdf')) return 'pdf'
  if (/\.(png|jpg|jpeg|gif|webp|svg)$/i.test(lower)) return 'image'
  if (/\.(eml|msg)$/i.test(lower)) return 'email'
  if (/\.(docx?|odt|rtf)$/i.test(lower)) return 'docx'
  if (/\.(xlsx?|csv|ods)$/i.test(lower)) return 'sheet'
  if (/\.(txt|md)$/i.test(lower)) return 'text'
  return 'other'
}
