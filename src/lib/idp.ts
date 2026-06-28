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

export const idp = {
  ocr:       (id: string)                              => callIdp<IDPResult>('ocr',       id),
  summarize: (id: string, style = 'brief')             => callIdp<IDPResult>('summarize', id, { style }),
  qa:        (id: string, question: string)            => callIdp<IDPResult>('qa',        id, { question }),
  tables:    (id: string)                              => callIdp<IDPTables>('tables',    id),
  entities:  (id: string)                              => callIdp<IDPEntities>('entities', id),
  classify:  (id: string)                              => callIdp<IDPClassify>('classify', id),
  translate: (id: string, target: string)              => callIdp<IDPResult>('translate', id, { target }),
  redact:    (id: string, categories: string[])        => callIdp<IDPResult>('redact',    id, { categories }),
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
