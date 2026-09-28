/**
 * Display helpers shared by every Knowledge-graph view (Pipeline, Network,
 * Entities, Layers): friendly document names, markdown → one-line previews and
 * the knowledge-store ("kg-ingest") counts summary.
 *
 * Pure (no React / DOM imports) so it can be bundled and checked in Node.
 */

// ── Friendly document names ────────────────────────────────────────────────

export interface FriendlyDocHints {
  /** Document kind (pdf / image / docx …) — only used for empty names. */
  kind?: string | null
  /** Laya's role for the document in a run (subject / reference / signature_reference …). */
  role?: string | null
  /** The document is a signature reference card (kg entity props.signature_card). */
  signatureCard?: boolean
  /** Person names to strip from the file name ("…_Michel_Neufeglise.png"). */
  ownerNames?: string[]
}

/** Well-known document types, matched on the cleaned, lower-cased, accent-free name. */
const DOC_PHRASES: [RegExp, string][] = [
  [/\bhand\s*written\b.*\bletter\b|\bletter\b.*\bhand\s*written\b/, 'Handwritten letter'],
  [
    /\b(?:company\s+)?(?:registry|register|kvk|handelsregister)\s+(?:extract|uittreksel)\b|\b(?:registry|kvk)\b.*\bextract\b|\buittreksel\b|\bkvk\b/,
    'Company registry extract',
  ],
  [/\bsignature\s*(?:card|specimens?|samples?|references?)\b|\bspecimen\s+signatures?\b/, 'Signature card'],
  [/^(?:cv|resume|curriculum\s+vitae|curriculum)\b|\b(?:cv|resume|curriculum\s+vitae)$/, 'CV'],
]

/** Lower-case, accent-free (é → e) copy. */
export function foldText(s: string): string {
  return String(s || '')
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
}

function fileStem(filename: string): string {
  const name = String(filename || '').split(/[\\/]/).pop() || ''
  const m = /^(.*)\.([A-Za-z0-9]{1,5})$/.exec(name)
  return (m && m[1] ? m[1] : name).trim()
}

function nameWords(names: string[] | undefined): Set<string> {
  const out = new Set<string>()
  for (const n of names || []) {
    const words = foldText(n).split(/[^a-z0-9]+/).filter(w => w.length >= 2)
    for (const w of words) out.add(w)
    if (words.length > 1) out.add(words.join('')) // "michelneufeglise"
  }
  return out
}

/**
 * A readable name for an uploaded file:
 * "DEMO_handwritten_letter_Michel_Neufeglise.png" → "Handwritten letter",
 * "DEMO_company_registry_extract_…pdf" → "Company registry extract",
 * "DEMO_signature_card_…png" → "Signature card", "cv_michelneufeglise.docx" → "CV",
 * "business-report_q3.pdf" → "Business report q3". Strips the extension, a DEMO
 * prefix, underscores / dashes and the owner's name; falls back to the raw stem.
 */
export function friendlyDocName(filename: string, hints: FriendlyDocHints = {}): string {
  const stem = fileStem(filename)
  if (!stem) return String(filename || '') || 'Document'
  let tokens = stem
    .replace(/[_]+/g, ' ')
    .replace(/(\S)-(?=\S)/g, '$1 ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .filter(Boolean)
  // Leading "DEMO" / "demo" marker
  while (tokens.length > 1 && /^demo$/i.test(tokens[0])) tokens = tokens.slice(1)
  const cleanedLower = foldText(tokens.join(' '))

  for (const [re, label] of DOC_PHRASES) {
    if (re.test(cleanedLower)) return label
  }
  if (hints.signatureCard || hints.role === 'signature_reference') return 'Signature card'

  // Strip the owner's name tokens (keep at least one token).
  const owner = nameWords(hints.ownerNames)
  if (owner.size) {
    const kept = tokens.filter(t => !owner.has(foldText(t).replace(/[^a-z0-9]/g, '')))
    if (kept.length) tokens = kept
  }
  let out = tokens.join(' ').trim()
  if (!out) return stem
  // An id-like single token ("lt40zedjjwu31") stays as it is.
  if (tokens.length === 1 && /\d/.test(out) && /[a-z]/i.test(out) && !/\s/.test(out)) return out
  out = out.charAt(0).toUpperCase() + out.slice(1)
  return out
}

/**
 * Friendly names for a set of documents; names that collide ("CV" twice) fall
 * back to the cleaned name that keeps the person's name, then to the file name.
 */
export function friendlyDocNames(
  docs: { id: string; filename: string; hints?: FriendlyDocHints }[],
): Map<string, string> {
  const out = new Map<string, string>()
  const count = new Map<string, number>()
  for (const d of docs) {
    const n = friendlyDocName(d.filename, d.hints)
    out.set(d.id, n)
    count.set(n, (count.get(n) || 0) + 1)
  }
  // Colliding names: add only the words that tell the files apart —
  // "DEMO_handwritten_letter_FORGED_signature.png" → "Handwritten letter · FORGED signature",
  // while "DEMO_handwritten_letter_Michel_Neufeglise.png" keeps "Handwritten letter".
  const extraOf = new Map<string, string>()
  const plainLeft = new Map<string, number>()
  for (const d of docs) {
    const n = out.get(d.id)!
    if ((count.get(n) || 0) < 2) continue
    const known = new Set([...nameWords(d.hints?.ownerNames), ...foldText(n).split(/[^a-z0-9]+/).filter(Boolean), 'demo'])
    const extra = fileStem(d.filename)
      .split(/[\s_-]+/)
      .filter(t => t && !known.has(foldText(t).replace(/[^a-z0-9]/g, '')))
      .join(' ')
    extraOf.set(d.id, extra)
    if (!extra) plainLeft.set(n, (plainLeft.get(n) || 0) + 1)
  }
  for (const d of docs) {
    if (!extraOf.has(d.id)) continue
    const n = out.get(d.id)!
    const extra = extraOf.get(d.id)!
    if (extra) out.set(d.id, `${n} · ${extra}`)
    else if ((plainLeft.get(n) || 0) > 1) {
      const stem = fileStem(d.filename).replace(/^demo[_\s-]+/i, '').replace(/[_]+/g, ' ').trim()
      if (foldText(stem) !== foldText(n)) out.set(d.id, stem ? `${n} · ${stem}` : d.filename)
    }
  }
  return out
}

// ── Markdown → one-line plain preview ──────────────────────────────────────

function inlinePlain(s: string): string {
  return s
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '') // images
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1') // links
    .replace(/<br\s*\/?>/gi, ' ')
    .replace(/<\/?[a-z][^>]*>/gi, '') // html tags
    .replace(/`{1,3}([^`]*)`{1,3}/g, '$1')
    .replace(/(\*\*|__)(.+?)\1/g, '$2')
    .replace(/~~(.+?)~~/g, '$1')
    .replace(/(^|[\s(])[*_]([^*_\s][^*_]*?)[*_](?=[\s).,;:!?]|$)/g, '$1$2')
    .replace(/\\([\\`*_{}[\]()#+\-.!|])/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

/**
 * Markdown → readable single line: headings (`# X` or a line that is only
 * `**X**`) become "X — …", table rows become "a · b", list / quote markers and
 * emphasis are dropped. "**Letter**\nThe letter was…" → "Letter — The letter was…".
 */
export function markdownToPlain(md: string): string {
  const lines = String(md || '').replace(/\r/g, '').split('\n')
  const parts: string[] = []
  let inFence = false
  for (const raw of lines) {
    let line = raw.trim()
    if (/^(```|~~~)/.test(line)) {
      inFence = !inFence
      continue
    }
    if (!line) continue
    if (/^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/.test(line)) continue // table separator
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) continue // horizontal rule
    let heading = false
    const h = /^#{1,6}\s+(.*)$/.exec(line)
    if (h) {
      line = h[1]
      heading = true
    } else if (!inFence && /^(\*\*|__)[^*_]+\1:?$/.test(line)) {
      heading = true
    }
    if (line.startsWith('|')) {
      line = line
        .replace(/^\||\|$/g, '')
        .split('|')
        .map(c => c.trim())
        .filter(Boolean)
        .join(' · ')
    }
    line = line.replace(/^>\s*/, '').replace(/^([-*+•]|\d+[.)])\s+/, '')
    line = inlinePlain(line)
    if (!line) continue
    if (heading) parts.push(`${line.replace(/[:.]\s*$/, '')} —`)
    else parts.push(line)
  }
  return parts
    .join(' ')
    .replace(/ — (?=[^—]{1,40} — )/g, ' · ') // consecutive headings
    .replace(/\s*—\s*$/, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** One-line preview of a markdown answer, clipped to `max` characters. */
export function markdownPreview(md: string, max: number): string {
  const t = markdownToPlain(md)
  return t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t
}

// ── Knowledge-store (kg-ingest) counts ─────────────────────────────────────

export interface StoreCounts {
  /** Entities / relations the run touched (new or already known); null = unknown. */
  entities: number | null
  relations: number | null
  newEntities: number
  newRelations: number
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/**
 * Counts of a knowledge-store tile. New tiles carry `data` ({type:'kg_ingest',
 * entities, relations, new_entities, new_relations}) and a detail like
 * "5 entities (+2 new) · 7 relations (+4 new)"; older tiles only have the
 * new-row counts ("+0 entities · +0 relations · +1 mentions") plus the list of
 * touched entities in `items`.
 */
export function storeCounts(tile: { detail?: string | null; items?: unknown[] | null; data?: unknown } | null | undefined): StoreCounts | null {
  if (!tile) return null
  const d = tile.data as Record<string, unknown> | null | undefined
  if (d && d.type === 'kg_ingest') {
    const n = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0)
    return { entities: n(d.entities), relations: n(d.relations), newEntities: n(d.new_entities), newRelations: n(d.new_relations) }
  }
  const detail = String(tile.detail || '')
  const cur = /(\d+)\s+entit(?:y|ies)(?:\s*\(\+(\d+)\s+new\))?\s*·\s*(\d+)\s+relations?(?:\s*\(\+(\d+)\s+new\))?/i.exec(detail)
  if (cur) {
    return { entities: Number(cur[1]), relations: Number(cur[3]), newEntities: Number(cur[2] || 0), newRelations: Number(cur[4] || 0) }
  }
  const old = /\+(\d+)\s+entit\w*\s*·\s*\+(\d+)\s+relation/i.exec(detail)
  if (old) {
    const ne = Number(old[1])
    const items = Array.isArray(tile.items) ? tile.items.length : 0
    return { entities: Math.max(ne, items) || null, relations: null, newEntities: ne, newRelations: Number(old[2]) }
  }
  return null
}

/** "5 entities (+2 new) · 7 relations (+4 new)" / "5 entities · 7 relations · already known". */
export function storeSummary(tile: { detail?: string | null; items?: unknown[] | null; data?: unknown } | null | undefined): string {
  const c = storeCounts(tile)
  if (!c) return String(tile?.detail || '')
  const ent =
    c.entities != null
      ? `${plural(c.entities, 'entity', 'entities')}${c.newEntities ? ` (+${c.newEntities} new)` : ''}`
      : c.newEntities
        ? `+${plural(c.newEntities, 'new entity', 'new entities')}`
        : ''
  const rel =
    c.relations != null
      ? `${plural(c.relations, 'relation', 'relations')}${c.newRelations ? ` (+${c.newRelations} new)` : ''}`
      : c.newRelations
        ? `+${plural(c.newRelations, 'new relation', 'new relations')}`
        : ''
  const parts = [ent, rel].filter(Boolean)
  if (!c.newEntities && !c.newRelations) parts.push('already known')
  return parts.join(' · ')
}

const FILE_IN_TEXT_RE = /[^\s()[\]"'“”,;:]+\.(?:png|jpe?g|gif|webp|heic|tiff?|bmp|pdf|docx?|odt|rtf|xlsx|xlsm|xls|ods|csv|tsv|txt|md|eml|msg)\b/gi

/** Replace file names inside a sentence with their friendly names ("vs DEMO_signature_card_….png" → "vs Signature card"). */
export function friendlyFilesInText(text: string): string {
  return String(text || '').replace(FILE_IN_TEXT_RE, m => friendlyDocName(m))
}

/** `hf.co/org/Some-Model-GGUF:q4_k_m` → `Some-Model-GGUF:q4_k_m` (the full id belongs in a tooltip). */
export function shortModelName(model: string | null | undefined): string {
  if (!model) return ''
  const parts = String(model).split('/')
  return parts[parts.length - 1] || String(model)
}
