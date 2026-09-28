/**
 * Guided "Replay" of a Documents run: a deterministic decision trail.
 *
 * `buildRunTrail` turns ONE doc-agent run (its tiles + messages) into ordered
 * steps that explain what happened and WHY (which model / tool was chosen for
 * what), each with the node / edge ids to highlight in the Pipeline view and
 * in the run graph (Network + Layers). `buildFactTrail` does the same for the
 * Entities view from the knowledge store: the focal person, then each
 * verification fact with where it came from.
 *
 * No LLM, no React, no DOM — bundled and checked in Node. Every field of a
 * tile is optional here: old runs with partial data yield fewer steps, never
 * a crash.
 */
import { signatureData, type Tile, type Decision, type TileItem } from '@/lib/docAgent'
import { friendlyDocName, friendlyDocNames, friendlyFilesInText, markdownPreview, shortModelName, storeCounts, foldText } from './kgFormat'
import { focalPerson } from './kgStory'
import { SOCIAL_PLATFORMS, isSocialProfileUrl, resolvePlatform } from './socialPlatforms'

// ── Types ─────────────────────────────────────────────────────────────────────

export type ReplayView = 'pipeline' | 'network' | 'layers' | 'entities'
/** Target sets: Pipeline, the run graph (Network + Layers share ids), Entities. */
export type ReplayTargetKey = 'pipeline' | 'run' | 'entities'

export interface ReplayTargets {
  /** Node ids; the first one is the primary node (the callout anchors to it). */
  nodes: string[]
  /** Edge ids that lead into this step (drawn as a flowing edge). */
  edges: string[]
}

export type ReplayStepKind =
  | 'question'
  | 'intent'
  | 'router'
  | 'roles'
  | 'read'
  | 'table'
  | 'kg-query'
  | 'web-plan'
  | 'web-search'
  | 'profile'
  | 'signature'
  | 'signature-vision'
  | 'context'
  | 'answer-model'
  | 'answer'
  | 'store'
  | 'person'
  | 'fact'

export type ReplayTone = 'ok' | 'warn' | 'bad' | 'info' | 'neutral'

export interface ReplayChip {
  label: string
  tone?: 'model' | 'ok' | 'warn' | 'bad' | 'info' | 'muted'
}

export interface ReplayStep {
  id: string
  kind: ReplayStepKind
  /** Small uppercase label above the title ("Decision", "Tool", …). */
  eyebrow: string
  title: string
  /** 1–2 plain sentences: what happened AND why. */
  body: string
  chips: ReplayChip[]
  /** Short, speakable text (no markdown, no file names). */
  narration: string
  tone: ReplayTone
  targets: Partial<Record<ReplayTargetKey, ReplayTargets>>
  /** Entities: the relation this fact is about (story strip + evidence). */
  relationId?: string
}

export interface RunTrailInput {
  /** Run key = assistant message id (run-graph node ids use it). */
  runKey: string
  question: string
  tiles: Tile[] | unknown
  answer?: string | null
  /** Assistant message meta (intent, stats, error …). */
  meta?: Record<string, unknown> | null
  /** User message attachments ({doc_id, name, kind, role}). */
  attachments?: unknown
  /** Assistant message model (fallback for the answer model). */
  model?: string | null
}

export const TARGET_KEY: Record<ReplayView, ReplayTargetKey> = {
  pipeline: 'pipeline',
  network: 'run',
  layers: 'run',
  entities: 'entities',
}

/** The steps that have something to show in `view`. */
export function stepsForView(steps: ReplayStep[], view: ReplayView): ReplayStep[] {
  const key = TARGET_KEY[view]
  return steps.filter(s => (s.targets[key]?.nodes.length ?? 0) > 0)
}

// ── Timing ────────────────────────────────────────────────────────────────────

/** Auto-advance time for a step: ~4 s base + reading time for longer bodies, halved at 2×. */
export function stepDurationMs(step: Pick<ReplayStep, 'body' | 'title'>, speed: number): number {
  const chars = (step.body || '').length + (step.title || '').length
  const base = 4000 + Math.max(0, chars - 100) * 22
  return Math.round(Math.min(10000, base) / (speed > 0 ? speed : 1))
}

// ── Formatting helpers ────────────────────────────────────────────────────────

const str = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' && Number.isFinite(v) ? String(v) : '')
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

function clip(text: string, max: number): string {
  const t = String(text || '').replace(/\s+/g, ' ').trim()
  return t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t
}

const fmtInt = (n: number) => Math.round(n).toLocaleString('en-US')
const plural = (n: number, one: string, many = `${one}s`) => `${fmtInt(n)} ${n === 1 ? one : many}`
const pct = (conf: number | null | undefined) => (typeof conf === 'number' && Number.isFinite(conf) ? `${Math.round(conf * 100)}%` : '')

const SPOKEN_NUMBERS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten', 'eleven', 'twelve']
const spokenCount = (n: number) => (n >= 0 && n < SPOKEN_NUMBERS.length ? SPOKEN_NUMBERS[n] : fmtInt(n))

/** "1.2 s", "19 s", "1 min 56 s". */
export function fmtDuration(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return ''
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 10000) return `${(ms / 1000).toFixed(1)} s`
  if (ms < 60000) return `${Math.round(ms / 1000)} s`
  const m = Math.floor(ms / 60000)
  const s = Math.round((ms % 60000) / 1000)
  return s ? `${m} min ${s} s` : `${m} min`
}

/** "about 19 seconds", "about 2 minutes" — for narration. */
export function spokenDuration(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms <= 0) return ''
  if (ms < 1500) return 'about a second'
  if (ms < 60000) return `about ${Math.round(ms / 1000)} seconds`
  const m = Math.round(ms / 60000)
  return m <= 1 ? 'about a minute' : `about ${m} minutes`
}

/** `hf.co/org/baidu.Unlimited-OCR-GGUF:q4_k_m` → `Unlimited-OCR`, `qwen3:4b-instruct-2507-q4_K_M` → `qwen3:4b`. */
export function displayModel(model: string | null | undefined): string {
  if (!model) return ''
  const short = shortModelName(model)
  const [baseRaw, tagRaw = ''] = short.split(':')
  let base = baseRaw.replace(/-GGUF$/i, '')
  // "baidu.Unlimited-OCR" → "Unlimited-OCR" (org prefix before a dot)
  const dot = /^[a-z0-9_-]+\.(.+)$/i.exec(base)
  if (dot && !/^\d/.test(dot[1])) base = dot[1]
  const size = /^(\d+(?:\.\d+)?[bm])(?![a-z])/i.exec(tagRaw)
  if (size) return `${base}:${size[1].toLowerCase()}`
  if (!tagRaw || /^q\d|^fp\d|^latest$/i.test(tagRaw)) return base
  return `${base}:${tagRaw}`
}

const FAMILY: [RegExp, string][] = [
  [/^gemma/i, 'Gemma'],
  [/^qwen/i, 'Qwen'],
  [/^deepseek/i, 'DeepSeek'],
  [/^llama/i, 'Llama'],
  [/^mistral/i, 'Mistral'],
  [/^phi/i, 'Phi'],
  [/^granite/i, 'Granite'],
]

/** Speakable model name: `gemma4:12b` → "Gemma 4, 12B", `deepseek-r1:14b` → "DeepSeek R1, 14B". */
export function spokenModel(model: string | null | undefined): string {
  const d = displayModel(model)
  if (!d) return 'the model'
  if (d === 'Laya') return 'Laya'
  const [base, size] = d.split(':')
  let words = base
    .replace(/[-_.]+/g, ' ')
    .replace(/([a-z]{2,})(\d)/gi, '$1 $2')
    .split(' ')
    .filter(Boolean)
    .map(w => (/^r\d$/i.test(w) ? w.toUpperCase() : /^ocr$/i.test(w) ? 'OCR' : w))
    .join(' ')
  for (const [re, name] of FAMILY) {
    if (re.test(words)) {
      words = words.replace(re, name)
      break
    }
  }
  words = words.replace(/\s+(\d)\s*$/, ' $1') // "Qwen 3"
  const sz = size ? /^(\d+(?:\.\d+)?)([bm])$/i.exec(size) : null
  return sz ? `${words}, ${sz[1]}${sz[2].toUpperCase()}` : words
}

const INTENT_LABEL: Record<string, string> = {
  cross_reference: 'cross-reference',
  identify_person: 'identify a person',
  verify_signature: 'signature verification',
  general_question: 'general question',
  graph_query: 'knowledge-graph question',
  summarize: 'summary',
  summarise: 'summary',
  extract_fields: 'field extraction',
  compare: 'comparison',
  table_query: 'spreadsheet question',
  translate: 'translation',
}
export function intentLabel(intent: string | null | undefined): string {
  const k = String(intent || '').trim()
  if (!k) return ''
  return INTENT_LABEL[k] || k.replace(/_/g, ' ')
}

const INTENT_NOUN: Record<string, string> = {
  identify_person: 'person-identification',
  general_question: 'general',
  graph_query: 'knowledge-graph',
  table_query: 'spreadsheet',
}
/** "cross-reference requests", "person-identification requests". */
function intentRequests(intent: string): string {
  return `${INTENT_NOUN[intent] || intentLabel(intent)} requests`
}
const INTENT_PHRASE: Record<string, string> = {
  identify_person: 'a request to identify a person',
  general_question: 'a general question',
}
/** "a cross-reference", "a request to identify a person". */
function intentPhrase(intent: string): string {
  const k = String(intent || '')
  if (INTENT_PHRASE[k]) return INTENT_PHRASE[k]
  const l = intentLabel(k) || 'request'
  return `${/^[aeiou]/i.test(l) ? 'an' : 'a'} ${l}`
}

const ROLE_LABEL: Record<string, string> = {
  subject: 'subject',
  reference: 'reference',
  signature_reference: 'signature reference',
}
const roleLabel = (r: string) => ROLE_LABEL[r] || String(r || '').replace(/_/g, ' ')

/** "the Handwritten letter" → "the handwritten letter" (keeps acronyms like CV). */
function lowerName(name: string): string {
  if (!name) return name
  const first = name.split(' ')[0]
  if (first.length > 1 && first === first.toUpperCase()) return name
  return name.charAt(0).toLowerCase() + name.slice(1)
}

function joinList(items: string[], and = 'and'): string {
  const l = items.filter(Boolean)
  if (l.length <= 1) return l.join('')
  return `${l.slice(0, -1).join(', ')} ${and} ${l[l.length - 1]}`
}

/** "The" + name, unless the friendly name is a raw file id. */
function theDoc(name: string): string {
  return `the ${lowerName(name)}`
}
/** Sentence-initial variant: "The handwritten letter". */
function TheDoc(name: string): string {
  return `The ${lowerName(name)}`
}

function firstSentence(text: string, max: number): string {
  const plain = markdownPreview(text || '', 600)
  const m = /^(.{20,}?[.!?])(\s|$)/.exec(plain)
  return clip(m ? m[1] : plain, max)
}

function charsOf(detail: string): number | null {
  const m = /(\d[\d,]*)\s*chars?\b/i.exec(detail || '')
  return m ? Number(m[1].replace(/,/g, '')) : null
}

// ── Run trail ─────────────────────────────────────────────────────────────────

interface RunDoc {
  id: string
  filename: string
  role: string | null
  name: string
}

function asTiles(raw: unknown): Tile[] {
  if (!Array.isArray(raw)) return []
  return raw.filter((t): t is Tile => !!t && typeof t === 'object' && typeof (t as Tile).kind === 'string') as Tile[]
}

function asDecisions(raw: unknown): Decision[] {
  if (!Array.isArray(raw)) return []
  return raw.filter((d): d is Decision => !!d && typeof d === 'object' && typeof (d as Decision).id === 'string')
}

function items(t: Tile | undefined): TileItem[] {
  return Array.isArray(t?.items) ? (t!.items as TileItem[]).filter(i => !!i && typeof i === 'object') : []
}

export function buildRunTrail(input: RunTrailInput): ReplayStep[] {
  const steps: ReplayStep[] = []
  const K = String(input?.runKey || '')
  if (!K) return steps
  const tiles = asTiles(input.tiles)
  const meta = (input.meta && typeof input.meta === 'object' ? input.meta : {}) as Record<string, unknown>
  const question = String(input.question || '').trim()

  const byId = (id: string) => tiles.find(t => t.id === id)
  const byKind = (...kinds: string[]) => tiles.find(t => kinds.includes(t.kind))
  const laya = byId('laya') ?? byKind('laya')
  const decisions = asDecisions(laya?.decisions)
  const decision = (id: string) => decisions.find(d => d.id === id)
  const answerTile = byId('answer') ?? byKind('llm', 'vision')
  const extractTiles = tiles.filter(t => t.id?.startsWith?.('extract-') || t.kind === 'extract' || t.kind === 'ocr')
  const planTile = byId('web-plan') ?? byKind('planner')
  const webTile = byId('web-search') ?? byKind('web')
  const sigTile = byId('signature-check') ?? byKind('signature')
  const tableTiles = tiles.filter(t => t.kind === 'table')
  const storeLike = tiles.filter(t => t.kind === 'store' || t.kind === 'query') // same order/index as kgNetwork

  // ── Documents in this run (attachments, tile docs, role decisions) ──
  const docMap = new Map<string, RunDoc>()
  const addDoc = (id: string, filename: string, role: string | null) => {
    if (!id) return
    const prev = docMap.get(id)
    if (prev) {
      if (!prev.filename && filename) prev.filename = filename
      if (!prev.role && role) prev.role = role
      return
    }
    docMap.set(id, { id, filename: filename || '', role, name: '' })
  }
  if (Array.isArray(input.attachments)) {
    for (const a of input.attachments as any[]) {
      if (!a || typeof a !== 'object') continue
      addDoc(str(a.doc_id), str(a.name), a.role && a.role !== 'auto' ? str(a.role) : null)
    }
  }
  for (const t of tiles) {
    if (t.doc && typeof t.doc === 'object') addDoc(str(t.doc.doc_id), str(t.doc.name), null)
  }
  for (const d of decisions) {
    if (!d.id.startsWith('role-')) continue
    const id = d.id.slice(5)
    const m = /\((.*)\)/.exec(d.label || '')
    addDoc(id, m ? m[1].trim() : '', null)
    const doc = docMap.get(id)
    if (doc && d.value) doc.role = String(d.value)
  }
  const docs = [...docMap.values()]
  const names = friendlyDocNames(docs.map(d => ({ id: d.id, filename: d.filename || d.id, hints: { role: d.role } })))
  for (const d of docs) d.name = names.get(d.id) || friendlyDocName(d.filename || d.id, { role: d.role })
  const docName = (id: string) => docMap.get(id)?.name || 'document'
  const docNode = (id: string) => `doc:${id}`
  const docEdge = (id: string) => `e:${K}:doc:${id}`

  const intentDec = decision('intent')
  const intent = str(intentDec?.value) || str(meta.intent)
  const amDec = decision('answer_model')
  const answerModel = str(amDec?.value) || str(answerTile?.model) || str(input.model)

  // 1 ── Question ──────────────────────────────────────────────────────────
  {
    const q = question || '(no question text)'
    const docList = docs.map(d => d.name)
    const body = docs.length
      ? `“${clip(q, 150)}” — asked about ${docs.length === 1 ? theDoc(docList[0]) : `${plural(docs.length, 'document')}: ${joinList(docList)}`}.`
      : `“${clip(q, 180)}”`
    steps.push({
      id: 'question',
      kind: 'question',
      eyebrow: 'Question',
      title: 'Your question',
      body,
      chips: docs.length ? [{ label: plural(docs.length, 'document'), tone: 'muted' }] : [],
      narration: docs.length
        ? `It starts with your question, about ${docs.length === 1 ? theDoc(docList[0]) : `${spokenCount(docs.length)} documents: ${joinList(docList.map(lowerName))}`}.`
        : 'It starts with your question.',
      tone: 'neutral',
      targets: {
        pipeline: { nodes: ['question'], edges: [] },
        run: { nodes: [`q:${K}`, ...docs.map(d => docNode(d.id))], edges: docs.map(d => docEdge(d.id)) },
      },
    })
  }

  // 2 ── Intent (Laya / rules) ─────────────────────────────────────────────
  if (laya && (intentDec || intent)) {
    const label = intentLabel(intent) || 'unknown'
    const note = str(intentDec?.note)
    const src = str(intentDec?.source)
    const conf = pct(intentDec?.confidence)
    const params = str((laya.model_info as Record<string, unknown> | null)?.params)
    const layaDesc = `Laya, a small local classifier${params ? ` (${params} parameters)` : ''} that labels requests without generating text`
    let body: string
    let narration: string
    const chips: ReplayChip[] = [{ label: label, tone: 'info' }]
    const matched = /keyword rules matched:\s*([^·]+)/i.exec(note)
    const suggested = /Laya suggested\s+([a-z_]+)\s*\(([\d.]+)\)/i.exec(note)
    const unsure = /Laya (?:unsure|uncertain)(?:\s*\(([\d.]+)\))?/i.exec(note)
    if (src === 'rules' && matched) {
      const phrase = clip(matched[1].split('/').pop()!.trim(), 60)
      body = `Keyword rules recognised explicit wording (“${phrase}”), so this is ${intentPhrase(intent)}.`
      if (suggested) {
        body += ` Laya's own guess was ${intentLabel(suggested[1])} (${pct(Number(suggested[2]))}); explicit wording wins, and the signature part is handled as an extra step.`
      }
      narration = `Keyword rules recognised the wording, so this is ${intentPhrase(intent)}${suggested ? `. Laya suggested ${intentLabel(suggested[1])}, but the explicit wording wins` : ''}.`
      chips.push({ label: 'keyword rules', tone: 'muted' })
      if (suggested) chips.push({ label: `Laya: ${intentLabel(suggested[1])} ${pct(Number(suggested[2]))}`, tone: 'muted' })
    } else if (unsure) {
      const p = unsure[1] ? pct(Number(unsure[1])) : ''
      body = /defaulted/i.test(note)
        ? `Laya was not confident about this question, so Persephone fell back to a ${label}.`
        : `Laya was unsure${p ? ` (${p})` : ''}, so keyword rules decided: ${label}.`
      narration = /defaulted/i.test(note)
        ? `Laya was not confident, so Persephone treated it as a ${label}.`
        : `Laya was unsure, so keyword rules decided this is a ${label}.`
      chips.push({ label: p ? `Laya ${p}` : 'Laya unsure', tone: 'warn' })
    } else if (src === 'laya' || (!src && conf)) {
      body = `${layaDesc}, read the question and classified it as ${intentPhrase(intent)}${conf ? ` with ${conf} confidence` : ''}${/rules agree/i.test(note) ? '; keyword rules agree' : ''}.`
      narration = `Laya, the local decision model, classified the question as ${intentPhrase(intent)}${conf ? `, ${conf.replace('%', ' percent')} confident` : ''}.`
      if (conf) chips.push({ label: conf, tone: 'ok' })
      if (/rules agree/i.test(note)) chips.push({ label: 'rules agree', tone: 'muted' })
    } else {
      body = `The request was classified as ${label}${src ? ` (decided by ${src === 'user' ? 'you' : src})` : ''}.`
      narration = `The request was classified as ${label}.`
    }
    chips.push({ label: 'Laya', tone: 'model' })
    steps.push({
      id: 'intent',
      kind: 'intent',
      eyebrow: 'Decision · intent',
      title: `Intent: ${label}`,
      body,
      chips,
      narration,
      tone: 'info',
      targets: {
        pipeline: { nodes: ['encoder'], edges: ['question->encoder'] },
        run: { nodes: [`dec:${K}`, 'model:Laya'], edges: [`e:${K}:q-dec`, `e:${K}:laya`] },
      },
    })
  }

  // 3 ── Router: extra steps ───────────────────────────────────────────────
  {
    const web = decision('web_lookup')
    const sig = decision('signature_check')
    const tool = decision('tool')
    if (web || sig || tool) {
      const parts: string[] = []
      const why: string[] = []
      const chips: ReplayChip[] = []
      const said: string[] = []
      const sigOn = !!sig && !!sig.value && !/^none$|^no\b/i.test(String(sig.value))
      const webOn = !!web && !!web.value && String(web.value) !== 'none'
      if (sigOn) {
        const refFile = /vs\s+(.+)$/.exec(String(sig!.value))?.[1]?.trim() || /reference:\s*([^\s(]+\.\w+)/.exec(str(sig!.note))?.[1] || ''
        const refName = refFile ? friendlyDocName(refFile, { role: 'signature_reference' }) : 'the reference card'
        parts.push(`a signature check against ${refFile ? theDoc(refName) : refName}`)
        why.push(`the question asks about the signature and a reference card was attached${sig!.confidence != null ? ` (Laya ${pct(sig!.confidence)})` : ''}`)
        chips.push({ label: 'signature check', tone: 'info' })
        said.push('a signature check')
      }
      if (webOn) {
        const target = String(web!.value)
        const pretty = target
          .split(/[,+/ ]+/)
          .filter(Boolean)
          .map(t => SOCIAL_PLATFORMS[t as keyof typeof SOCIAL_PLATFORMS]?.label || t)
          .join(', ')
        parts.push(`a web lookup on ${pretty}`)
        why.push(`you asked to check ${pretty}; it is the only step that leaves this machine (the person's name goes to DuckDuckGo)`)
        chips.push({ label: `web: ${pretty}`, tone: 'warn' })
        said.push(`a web lookup on ${pretty}`)
      }
      if (tool && tool.value) {
        parts.push(`the ${String(tool.value)} tool`)
        why.push('the answer is already in the knowledge store, so documents need not be re-read')
        chips.push({ label: `tool: ${String(tool.value)}`, tone: 'info' })
        said.push(`the ${String(tool.value)} tool`)
      }
      let body: string
      let narration: string
      let title: string
      if (parts.length) {
        title = 'Router: extra steps'
        body = `The router added ${joinList(parts)} — because ${joinList(why)}.`
        narration = `The router then added ${joinList(said)}.`
      } else {
        title = 'Router: documents only'
        body = 'No web lookup, signature check or other tool was needed, so the answer relies on the documents alone.'
        narration = 'No extra tools were needed; the answer relies on the documents alone.'
        chips.push({ label: 'web: off', tone: 'muted' })
      }
      steps.push({
        id: 'router',
        kind: 'router',
        eyebrow: 'Decision · tools',
        title,
        body,
        chips,
        narration,
        tone: 'info',
        targets: {
          pipeline: { nodes: ['toolselect'], edges: ['encoder->toolselect'] },
          run: { nodes: [`dec:${K}`], edges: [`e:${K}:q-dec`] },
        },
      })
    }
  }

  // 4 ── Document roles ────────────────────────────────────────────────────
  {
    const roleDecs = decisions.filter(d => d.id.startsWith('role-') && d.value)
    if (roleDecs.length) {
      const lines = roleDecs.map(d => {
        const id = d.id.slice(5)
        const note = str(d.note)
        const extra = /company registry extract/i.test(note) ? ' for the company check' : ''
        return `${docName(id)} → ${roleLabel(String(d.value))}${extra}`
      })
      const bySrc = new Set(roleDecs.map(d => d.source))
      const how = bySrc.has('laya') && bySrc.size === 1 ? 'Laya' : bySrc.has('laya') ? 'file names, keyword rules and Laya' : 'file names and keyword rules'
      const spoken = roleDecs.map(d => {
        const id = d.id.slice(5)
        const r = String(d.value)
        return r === 'signature_reference'
          ? `${theDoc(docName(id))} holds the reference signatures`
          : `${theDoc(docName(id))} is the ${roleLabel(r)}`
      })
      steps.push({
        id: 'roles',
        kind: 'roles',
        eyebrow: 'Decision · roles',
        title: 'Document roles',
        body: `Each document got a role (from ${how}): ${lines.join('; ')}. The roles shape the instruction prompt.`,
        chips: roleDecs.slice(0, 3).map(d => ({ label: roleLabel(String(d.value)), tone: 'muted' as const })),
        narration: `Each document got a role: ${joinList(spoken)}.`,
        tone: 'neutral',
        targets: {
          pipeline: { nodes: ['instruction'], edges: [] },
          run: {
            nodes: roleDecs.map(d => docNode(d.id.slice(5))),
            edges: roleDecs.map(d => docEdge(d.id.slice(5))),
          },
        },
      })
    }
  }

  // 5 ── Reading each document ─────────────────────────────────────────────
  {
    const readable = extractTiles.filter(t => t.status !== 'skipped')
    const MAX_READ = 4
    readable.slice(0, MAX_READ).forEach(t => {
      const id = str(t.doc?.doc_id) || String(t.id || '').replace(/^(extract|ocr)-/, '')
      const name = docName(id) !== 'document' ? docName(id) : friendlyDocName(str(t.doc?.name) || id)
      const detail = str(t.detail)
      const chars = charsOf(detail)
      const model = str(t.model)
      const cached = /cached/i.test(detail)
      const ms = num(t.ms)
      const hw = decision(`handwritten-${id}`)
      const ocrNeed = decision(`ocr_needed-${id}`)
      const chips: ReplayChip[] = []
      let title: string
      let body: string
      let narration: string
      let tone: ReplayTone = 'neutral'
      if (t.status === 'error') {
        title = `Reading ${lowerName(name)} failed`
        body = `Persephone could not read ${theDoc(name)}: ${clip(friendlyFilesInText(detail) || 'unknown error', 140)}.`
        narration = `Reading ${theDoc(name)} failed.`
        tone = 'bad'
      } else if (/text layer/i.test(detail) && !model) {
        title = `${name}: text layer`
        body = `${TheDoc(name)} already has a text layer${chars ? ` (${fmtInt(chars)} characters)` : ''}, so it was read directly — no OCR or vision model needed.`
        narration = `${TheDoc(name)} already has a text layer, so no OCR was needed.`
        chips.push({ label: 'text layer', tone: 'ok' }, { label: 'no OCR', tone: 'muted' })
      } else if (/handwritten/i.test(detail) || String(hw?.value) === 'yes') {
        const askedHw = /asked\s+(\S+)/i.exec(str(hw?.note))
        const hwWhy = /file name/i.test(str(hw?.note))
          ? ' Its file name marked it as handwritten.'
          : askedHw
            ? ` ${displayModel(askedHw[1].replace(/[→>].*$/, ''))} checked first: handwritten, not printed.`
            : ''
        title = `${name}: handwriting`
        body = `Handwriting defeats plain OCR, so the vision model ${displayModel(model) || 'a vision model'} transcribed ${theDoc(name)}${cached ? ', reusing the transcript cached from an earlier run' : ''}${chars ? ` (${fmtInt(chars)} characters${ms && !cached ? `, ${fmtDuration(ms)}` : ''})` : ''}.${hwWhy}`
        narration = `Handwriting defeats plain OCR, so the vision model ${spokenModel(model)} transcribed ${theDoc(name)}${cached ? ', from cache' : ''}.`
        if (model) chips.push({ label: displayModel(model), tone: 'model' })
        chips.push({ label: 'handwriting', tone: 'info' })
        if (cached) chips.push({ label: 'cached', tone: 'muted' })
      } else if (t.kind === 'ocr' || /\bocr\b/i.test(detail) || model) {
        const printed = String(hw?.value) === 'no' && /asked\s+(\S+)/i.exec(str(hw?.note))
        const noLayer = ocrNeed ? 'has no text layer' : 'is an image'
        title = `${name}: OCR`
        body = `${TheDoc(name)} ${noLayer}, so ${model ? `the OCR model ${displayModel(model)}` : 'OCR'} read it${cached ? ' (reused from an earlier run)' : ''}${chars ? ` — ${fmtInt(chars)} characters${ms ? ` in ${fmtDuration(ms)}` : ''}` : ''}.${printed ? ` ${displayModel(printed[1].replace(/[→>].*$/, ''))} checked first: printed, not handwritten, so plain OCR was enough.` : ''}`
        narration = `${TheDoc(name)} ${noLayer}, so ${model ? `the OCR model ${spokenModel(model)}` : 'OCR'} read it.`
        if (model) chips.push({ label: displayModel(model), tone: 'model' })
        chips.push({ label: 'OCR', tone: 'info' })
        if (cached) chips.push({ label: 'cached', tone: 'muted' })
      } else {
        title = `Reading ${lowerName(name)}`
        body = `Persephone read ${theDoc(name)}${detail ? `: ${clip(friendlyFilesInText(detail), 140)}` : ''}.`
        narration = `Persephone read ${theDoc(name)}.`
      }
      if (chars) chips.push({ label: `${fmtInt(chars)} chars`, tone: 'muted' })
      if (ms && !cached) chips.push({ label: fmtDuration(ms), tone: 'muted' })
      const runNodes = [docNode(id)]
      const runEdges = [docEdge(id)]
      if (model && (t.kind === 'ocr' || t.kind === 'vision' || t.kind === 'extract')) {
        runNodes.push(`model:${model}`)
        runEdges.push(`e:${K}:tile-model:${t.id}`)
      }
      steps.push({
        id: `read:${id}`,
        kind: 'read',
        eyebrow: 'Reading',
        title,
        body,
        chips,
        narration,
        tone,
        targets: {
          pipeline: { nodes: ['tool-extract'], edges: ['tool-extract->toolselect', 'kg-lexical->tool-extract'] },
          run: { nodes: runNodes, edges: runEdges },
        },
      })
    })
    if (readable.length > MAX_READ) {
      const rest = readable.slice(MAX_READ)
      const ids = rest.map(t => str(t.doc?.doc_id) || String(t.id || '').replace(/^(extract|ocr)-/, ''))
      steps.push({
        id: 'read:more',
        kind: 'read',
        eyebrow: 'Reading',
        title: `${plural(rest.length, 'more document')}`,
        body: `Persephone also read ${joinList(ids.map(i => theDoc(docName(i))))}.`,
        chips: [],
        narration: `It also read ${spokenCount(rest.length)} more documents.`,
        tone: 'neutral',
        targets: {
          pipeline: { nodes: ['tool-extract'], edges: [] },
          run: { nodes: ids.map(docNode), edges: ids.map(docEdge) },
        },
      })
    }
  }

  // 6 ── Tools ─────────────────────────────────────────────────────────────
  // Table query (spreadsheets)
  tableTiles.forEach((t, i) => {
    const model = str(t.model)
    const q = items(t).find(it => it.kind === 'query')
    const ok = t.status === 'done'
    steps.push({
      id: `table:${i}`,
      kind: 'table',
      eyebrow: 'Tool · table query',
      title: ok ? 'Exact table query' : 'Table query failed',
      body: ok
        ? `Instead of letting a model guess numbers, ${model ? displayModel(model) : 'the model'} wrote a table query that pandas ran on the full sheet: ${clip(str(q?.label) || str(t.detail), 110)} → ${clip(str(t.detail), 60)}.`
        : `The table query failed (${clip(str(t.detail), 90)}), so the model answered from the sheet sample instead.`,
      chips: [model ? { label: displayModel(model), tone: 'model' as const } : null, { label: 'pandas', tone: 'info' as const }, t.ms ? { label: fmtDuration(num(t.ms)), tone: 'muted' as const } : null].filter(Boolean) as ReplayChip[],
      narration: ok
        ? `For the spreadsheet, ${spokenModel(model)} wrote an exact table query, and pandas computed the result.`
        : 'The table query failed, so the model answered from a sample of the sheet.',
      tone: ok ? 'info' : 'warn',
      targets: {
        pipeline: { nodes: ['tool-extract'], edges: ['tool-extract->toolselect'] },
        run: { nodes: [`dec:${K}`], edges: [] },
      },
    })
  })

  // Knowledge-graph query
  storeLike.forEach((t, i) => {
    if (t.kind !== 'query') return
    const facts = items(t).filter(it => it.kind === 'result').length
    const detail = clip(str(t.detail), 120)
    const ms = num(t.ms)
    steps.push({
      id: `kg-query:${i}`,
      kind: 'kg-query',
      eyebrow: 'Tool · knowledge graph',
      title: 'Knowledge-graph lookup',
      body: `Instead of re-reading documents, Persephone looked the question up in its local knowledge graph${detail ? `: ${detail}` : ''}${ms != null ? ` — in ${fmtDuration(ms)}, no LLM involved` : ''}.`,
      chips: [facts ? { label: plural(facts, 'fact'), tone: 'info' as const } : null, ms != null ? { label: fmtDuration(ms), tone: 'muted' as const } : null].filter(Boolean) as ReplayChip[],
      narration: `Instead of re-reading documents, it looked the question up in the knowledge graph${facts ? ` and found ${spokenCount(facts)} facts` : ''}.`,
      tone: 'info',
      targets: {
        pipeline: { nodes: ['tool-query'], edges: ['tool-query->toolselect', 'kg-domain->tool-query'] },
        run: { nodes: [`store:${K}:${i}`], edges: [`e:${K}:store:${i}`] },
      },
    })
  })

  // Web: plan → search → profile
  let webPlatform = ''
  if (planTile && planTile.status !== 'skipped') {
    const detail = str(planTile.detail)
    const queries = items(planTile).filter(it => it.kind === 'query')
    const [who, where] = detail.split('→').map(s => s.trim())
    webPlatform = where || ''
    const person = who ? who.replace(/^Person:\s*/i, '') : ''
    steps.push({
      id: 'web-plan',
      kind: 'web-plan',
      eyebrow: 'Tool · web plan',
      title: 'Planning the web lookup',
      body: `${person ? `Persephone took the person from the documents (${clip(person, 80)})` : 'Persephone took the person from the documents'} and planned ${plural(queries.length || 1, 'search', 'searches')}${where ? ` for ${where}` : ''}. Only these search terms leave the machine.`,
      chips: [queries.length ? { label: plural(queries.length, 'query', 'queries'), tone: 'info' as const } : null, where ? { label: where, tone: 'warn' as const } : null].filter(Boolean) as ReplayChip[],
      narration: `It planned ${spokenCount(queries.length || 1)} ${queries.length === 1 ? 'search' : 'searches'}${where ? ` on ${where}` : ''}, using only the person's name and role.`,
      tone: 'info',
      targets: {
        pipeline: { nodes: ['tool-web'], edges: ['tool-web->toolselect'] },
        run: { nodes: [`planner:${K}`], edges: [`e:${K}:planner`] },
      },
    })
  }
  if (webTile && webTile.status !== 'skipped') {
    const detail = str(webTile.detail)
    const its = items(webTile)
    const toolNote = its.find(it => it.kind === 'note' && /^Tool model:/i.test(str(it.label)))
    const tm = toolNote ? /^Tool model:\s*(\S+)\s*\(([^)]+)\)/i.exec(str(toolNote.label)) : null
    const toolModel = tm?.[1] || (str(webTile.model) !== 'direct search' ? str(webTile.model) : '')
    const toolSource = tm?.[2] || ''
    const verdictBy = /Verdict by\s+(\S+)/i.exec(its.map(it => str(it.label)).join('\n'))?.[1] || ''
    const early = its.some(it => /stopped early/i.test(str(it.label)))
    const searches = Number(/(\d+)\s+(?:live\s+)?search/i.exec(detail)?.[1] ?? its.filter(it => it.kind === 'query').length)
    const matches = Number(/(\d+)\s+matching/i.exec(detail)?.[1] ?? its.filter(it => it.kind === 'result').length)
    const cachedFallback = /earlier verification/i.test(detail)
    const blocked = webTile.status === 'error' || /blocked/i.test(detail)
    const direct = /direct search/i.test(detail) || str(webTile.model) === 'direct search'
    const vm = /\*\*([^*:]+):\*\*\s*([^—\n[(.]+)/.exec(str(webTile.output_preview))
    const verdict = vm ? vm[2].trim().replace(/\s+found$/i, '') : ''
    const ms = num(webTile.ms)
    const where = webPlatform || /·\s*(.+)$/.exec(str(webTile.title))?.[1]?.trim() || 'the web'
    let body: string
    let narration: string
    let tone: ReplayTone = 'info'
    if (cachedFallback) {
      body = `Live search was blocked by DuckDuckGo, so Persephone reported the earlier verification from its knowledge store instead — clearly marked as not live.`
      narration = 'Live search was blocked, so it used the earlier verification from the knowledge store, clearly marked as not live.'
      tone = 'warn'
    } else if (blocked) {
      body = `Every search was blocked by DuckDuckGo's bot detection, so no verdict was given — never a false “no match”.`
      narration = 'Every search was blocked, so no verdict was given rather than a false no match.'
      tone = 'warn'
    } else {
      const how = direct
        ? `No tool-calling model was available, so Persephone ran the planned searches directly`
        : toolModel
          ? `The small tool model ${displayModel(toolModel)}${toolSource ? ` (${toolSource})` : ''} ran ${plural(searches, 'search', 'searches')} through the local DuckDuckGo MCP tool`
          : `Persephone ran ${plural(searches, 'search', 'searches')} through the local DuckDuckGo MCP tool`
      body = `${how} and found ${plural(matches, 'matching result')}${early ? ' — then stopped early because the profile was found' : ''}.${verdict ? ` ${displayModel(verdictBy) || 'The answer model'} judged it: ${verdict.toLowerCase()}.` : ''}`
      narration = `${direct || !toolModel ? 'Persephone ran the searches through DuckDuckGo' : `The small tool model ${spokenModel(toolModel)} ran the search through DuckDuckGo`} and found ${spokenCount(matches)} matching ${matches === 1 ? 'result' : 'results'}${early ? ', then stopped early' : ''}.`
    }
    steps.push({
      id: 'web-search',
      kind: 'web-search',
      eyebrow: 'Tool · web search',
      title: `Web lookup · ${where}`,
      body,
      chips: [
        toolModel && !direct ? { label: displayModel(toolModel), tone: 'model' as const } : null,
        { label: 'DuckDuckGo MCP', tone: 'info' as const },
        !cachedFallback && !blocked ? { label: `${plural(searches, 'search', 'searches')} · ${plural(matches, 'match', 'matches')}`, tone: 'muted' as const } : null,
        early ? { label: 'early stop', tone: 'ok' as const } : null,
        cachedFallback ? { label: 'cached', tone: 'warn' as const } : null,
        ms ? { label: fmtDuration(ms), tone: 'muted' as const } : null,
      ].filter(Boolean) as ReplayChip[],
      narration,
      tone,
      targets: {
        pipeline: { nodes: ['tool-web'], edges: ['tool-web->toolselect', 'kg-lexical->tool-web'] },
        run: { nodes: [`web:${K}`], edges: [`e:${K}:web`] },
      },
    })
    // Profiles found (same filter as kgNetwork: social profile urls, max 3)
    const profiles = its.filter(it => it.kind === 'result' && it.url && isSocialProfileUrl(String(it.url))).slice(0, 3)
    if (profiles.length) {
      const p = profiles[0]
      const platform = resolvePlatform(p.platform, String(p.url))
      const label = platform ? SOCIAL_PLATFORMS[platform].label : 'Web'
      let host = ''
      try {
        host = new URL(String(p.url)).hostname.replace(/^www\./, '')
      } catch {
        host = ''
      }
      const title = clip(str(p.label).replace(/\s*[|\-–—·•/]\s*(?:LinkedIn|Facebook|Instagram|Twitter|X)\s*$/i, ''), 70)
      steps.push({
        id: 'profile',
        kind: 'profile',
        eyebrow: 'Result · profile',
        title: `${label} profile found`,
        body: `Found ${title ? `“${title}”` : host || 'a profile'}${cachedFallback ? ' (from the knowledge store, not live)' : ''}.${verdict ? ` ${displayModel(verdictBy) || 'The answer model'} compared it with the documents: ${verdict.toLowerCase()} — name, role and employer are checked.` : ''}`,
        chips: [{ label, tone: 'info' }, verdict ? { label: verdict.toLowerCase(), tone: /no match|not/i.test(verdict) ? 'warn' : 'ok' } : null].filter(Boolean) as ReplayChip[],
        narration: `It found a ${label} profile${verdict ? `, judged a ${verdict.toLowerCase()}` : ''}.`,
        tone: verdict && !/no match|not/i.test(verdict) ? 'ok' : 'info',
        targets: {
          pipeline: { nodes: ['tool-web'], edges: [] },
          run: { nodes: profiles.map((_, i) => `profile:${K}:${i}`), edges: profiles.map((_, i) => `e:${K}:profile:${i}`) },
        },
      })
    }
  }

  // Signature check: engine, then the vision model's second opinion
  if (sigTile && sigTile.status !== 'skipped') {
    const sd = signatureData(sigTile)
    const ms = num(sigTile.ms)
    const qName = sd?.questioned_doc ? friendlyDocName(sd.questioned_doc) : sigTile.doc?.doc_id ? docName(str(sigTile.doc.doc_id)) : 'document'
    const refFile = sd?.reference_docs?.[0] || ''
    const refName = refFile ? friendlyDocName(refFile, { role: 'signature_reference' }) : docs.find(d => d.role === 'signature_reference')?.name || 'reference card'
    const sigModel = str(sd?.model) || str(sigTile.model)
    const located = items(sigTile).find(it => /Signature located/i.test(str(it.label)))
    const locatedTxt = located ? /located:\s*([^—]+)/i.exec(str(located.label))?.[1]?.trim() : ''
    if (sd) {
      const tone: ReplayTone = sd.band === 'consistent' ? 'ok' : sd.band === 'inconclusive' ? 'warn' : 'bad'
      const bandSpoken = sd.band === 'consistent' ? 'consistent with the references' : sd.band === 'inconclusive' ? 'inconclusive' : 'inconsistent with the references'
      steps.push({
        id: 'signature',
        kind: 'signature',
        eyebrow: 'Tool · signature engine',
        title: `Signature: ${sd.score}% · ${sd.band}`,
        body: `The local signature engine — measurements, not an LLM — compared the signature on ${theDoc(qName)} with ${plural(sd.n_references, 'reference signature')} cut from ${theDoc(refName)}: ${sd.score}% — ${sd.band}.${locatedTxt ? ` ${displayModel(sigModel) || 'The vision model'} first located the signature (${locatedTxt}).` : ''}`,
        chips: [
          { label: `${sd.score}%`, tone: tone === 'ok' ? 'ok' : tone === 'warn' ? 'warn' : 'bad' },
          { label: sd.band, tone: tone === 'ok' ? 'ok' : tone === 'warn' ? 'warn' : 'bad' },
          { label: plural(sd.n_references, 'reference'), tone: 'muted' },
          { label: 'local engine', tone: 'info' },
          ...(ms ? [{ label: fmtDuration(ms), tone: 'muted' as const }] : []),
        ],
        narration: `The local signature engine compared the signature on ${theDoc(qName)} with ${spokenCount(sd.n_references)} reference signatures: ${sd.score} percent, ${bandSpoken}.`,
        tone,
        targets: {
          pipeline: { nodes: ['tool-signature'], edges: ['tool-signature->llm'] },
          run: { nodes: [`signature:${K}`], edges: [`e:${K}:signature`] },
        },
      })
      if (sigModel && (sd.assessment || sigModel)) {
        const quote = sd.assessment ? firstSentence(sd.assessment, 150) : ''
        steps.push({
          id: 'signature-vision',
          kind: 'signature-vision',
          eyebrow: 'Model · second opinion',
          title: `Visual check: ${displayModel(sigModel)}`,
          body: `The vision model ${displayModel(sigModel)} also looked at both signatures. The score comes from the engine; the model only describes what it sees${quote ? `: “${quote}”` : '.'}`,
          chips: [{ label: displayModel(sigModel), tone: 'model' }, { label: 'vision', tone: 'info' }],
          narration: `As a second opinion, the vision model ${spokenModel(sigModel)} looked at the signatures too, but the score comes from the engine.`,
          tone: 'info',
          targets: {
            pipeline: { nodes: ['tool-signature'], edges: [] },
            run: { nodes: [`model:${sigModel}`, `signature:${K}`], edges: [`e:${K}:tile-model:${sigTile.id}`] },
          },
        })
      }
    } else {
      steps.push({
        id: 'signature',
        kind: 'signature',
        eyebrow: 'Tool · signature engine',
        title: sigTile.status === 'error' ? 'Signature check failed' : 'Signature check',
        body: `Signature check: ${clip(friendlyFilesInText(str(sigTile.detail)) || str(sigTile.status), 150)}.`,
        chips: [],
        narration: sigTile.status === 'error' ? 'The signature check failed.' : 'Then the signature was checked.',
        tone: sigTile.status === 'error' ? 'bad' : 'info',
        targets: {
          pipeline: { nodes: ['tool-signature'], edges: ['tool-signature->llm'] },
          run: { nodes: [`signature:${K}`], edges: [`e:${K}:signature`] },
        },
      })
    }
  }

  // 7 ── Retrieved context (Pipeline) ──────────────────────────────────────
  {
    const pieces: string[] = []
    const used = extractTiles.filter(t => t.status !== 'skipped' && t.status !== 'error')
    const chars = used.reduce((s, t) => s + (charsOf(str(t.detail)) || 0), 0)
    if (used.length) pieces.push(`${chars ? `${fmtInt(chars)} characters of text` : 'the text'} from ${plural(used.length, 'document')}`)
    const facts = storeLike.filter(t => t.kind === 'query').reduce((s, t) => s + items(t).filter(i => i.kind === 'result').length, 0)
    if (facts) pieces.push(plural(facts, 'stored fact'))
    const tq = tableTiles.find(t => t.status === 'done')
    if (tq) pieces.push('the exact table-query result')
    const sd = signatureData(sigTile)
    if (sd) pieces.push(`the signature score (${sd.score}%)`)
    if (pieces.length) {
      steps.push({
        id: 'context',
        kind: 'context',
        eyebrow: 'Context',
        title: 'Retrieved context',
        body: `Everything gathered goes into one prompt for the answer model: ${joinList(pieces)}.`,
        chips: chars ? [{ label: `${fmtInt(chars)} chars`, tone: 'muted' }] : [],
        narration: `Everything gathered, ${joinList(pieces.map(p => p.replace(/\(\d+%\)/, '').replace(/\d[\d,]* characters of text/, 'the text').trim()))}, goes into one prompt.`,
        tone: 'neutral',
        targets: { pipeline: { nodes: ['context'], edges: ['toolselect->context'] } },
      })
    }
  }

  // 8 ── Answer model: which and why ───────────────────────────────────────
  if (answerModel) {
    const note = str(amDec?.note)
    const src = str(amDec?.source)
    const isVision = answerTile?.kind === 'vision' || /vision model/i.test(note)
    const cfgFor = /Text model for\s+([a-z_]+)/i.exec(note)
    const ocrSwap = /(\S+)\s+is an OCR-only model\s*—\s*using\s+(\S+)\s+instead/i.exec(note)
    let why: string
    let whySpoken: string
    if (ocrSwap) {
      why = `The configured model (${displayModel(ocrSwap[1])}) can only do OCR, so Persephone switched to ${displayModel(ocrSwap[2])}.`
      whySpoken = 'because the configured model can only do OCR'
    } else if (cfgFor) {
      why = `Laya only picks the intent; Settings → Models maps ${intentRequests(cfgFor[1])} to ${displayModel(answerModel)}, so that model writes the answer.`
      whySpoken = `because it's the model configured for ${intentRequests(cfgFor[1])}`
    } else if (isVision) {
      why = 'A vision model was chosen because the question needs to look at the images themselves.'
      whySpoken = 'because the question needs to look at the images'
    } else if (src === 'user') {
      why = 'You picked this model for the conversation.'
      whySpoken = 'because you picked it'
    } else if (note) {
      why = `${clip(friendlyFilesInText(note), 140)}.`
      whySpoken = ''
    } else {
      why = src === 'config' || !src ? 'It is the model configured for this kind of request.' : `Chosen by ${src}.`
      whySpoken = ''
    }
    const fallbacks = decisions.filter(d => d.id.startsWith('vision_fallback_') && d.value).map(d => displayModel(String(d.value)))
    if (fallbacks.length) why += ` Fallback: ${fallbacks.join(', ')}.`
    steps.push({
      id: 'answer-model',
      kind: 'answer-model',
      eyebrow: 'Decision · model',
      title: `Answer model: ${displayModel(answerModel)}`,
      body: why,
      chips: [
        { label: displayModel(answerModel), tone: 'model' },
        { label: isVision ? 'vision' : 'text model', tone: 'info' },
        src ? { label: src === 'config' ? 'Settings' : src === 'user' ? 'your choice' : src, tone: 'muted' as const } : null,
      ].filter(Boolean) as ReplayChip[],
      narration: `${spokenModel(answerModel)} was chosen to write the answer${whySpoken ? `, ${whySpoken}` : ''}.`,
      tone: 'info',
      targets: {
        pipeline: { nodes: ['llm'], edges: ['context->llm', 'instruction->llm', 'question->llm'] },
        run: { nodes: [`model:${answerModel}`], edges: [`e:${K}:answer`] },
      },
    })
  }

  // 9 ── Answer ─────────────────────────────────────────────────────────────
  {
    const answer = String(input.answer || '')
    const error = str(meta.error)
    const ms = num(answerTile?.ms)
    const total = num((meta.stats as Record<string, unknown> | undefined)?.total_ms)
    if (answerTile || answer || error) {
      const failed = !!error || answerTile?.status === 'error'
      const preview = answer ? friendlyFilesInText(firstSentence(answer.replace(/<think>[\s\S]*?(<\/think>|$)/gi, ' '), 150)) : ''
      const who = displayModel(answerModel) || 'The model'
      steps.push({
        id: 'answer',
        kind: 'answer',
        eyebrow: 'Answer',
        title: failed ? 'Answer failed' : ms ? `Answer written in ${fmtDuration(ms)}` : 'Answer written',
        body: failed
          ? `The answer failed: ${clip(error || str(answerTile?.detail) || 'unknown error', 140)}.`
          : `${who} wrote the answer${ms ? ` in ${fmtDuration(ms)}` : ''}${total && total !== ms ? ` (whole run ${fmtDuration(total)})` : ''}${preview ? `: “${preview}”` : '.'}`,
        chips: [
          answerModel ? { label: displayModel(answerModel), tone: 'model' as const } : null,
          ms ? { label: fmtDuration(ms), tone: 'muted' as const } : null,
          total ? { label: `run ${fmtDuration(total)}`, tone: 'muted' as const } : null,
        ].filter(Boolean) as ReplayChip[],
        narration: failed ? 'The answer failed.' : `${answerModel ? spokenModel(answerModel) : 'The model'} wrote the answer${ms ? ` in ${spokenDuration(ms)}` : ''}.`,
        tone: failed ? 'bad' : 'ok',
        targets: {
          pipeline: { nodes: ['answer'], edges: ['llm->answer'] },
          run: { nodes: [`q:${K}`], edges: answerModel ? [`e:${K}:answer`] : [] },
        },
      })
    }
  }

  // 10 ── Knowledge store ──────────────────────────────────────────────────
  storeLike.forEach((t, i) => {
    if (t.kind !== 'store' || t.status === 'skipped') return
    const c = storeCounts(t)
    const names = items(t)
      .map(it => /^(.*)\s+\((person|organization|role|profile|location)\)$/.exec(str(it.label)))
      .filter((m): m is RegExpExecArray => !!m)
      // "…and signer." → "…and signer" (a legal form such as "B.V." keeps its period)
      .map(m => m[1].trim().replace(/[\s,;:]+$/, '').replace(/(?<=[a-z]{3})\.+$/, '').replace(/\.{2,}$/, '.'))
      .filter(Boolean)
    const isNew = !!c && (c.newEntities > 0 || c.newRelations > 0)
    const counts = c
      ? [c.entities != null ? plural(c.entities, 'entity', 'entities') : '', c.relations != null ? plural(c.relations, 'relation') : ''].filter(Boolean).join(' and ')
      : ''
    const learned = isNew
      ? `New this run: ${joinList([c!.newEntities ? plural(c!.newEntities, 'entity', 'entities') : '', c!.newRelations ? plural(c!.newRelations, 'relation') : ''])}.`
      : c
        ? 'Nothing new: these facts were already known from earlier runs.'
        : ''
    steps.push({
      id: `store:${i}`,
      kind: 'store',
      eyebrow: 'Knowledge store',
      title: isNew ? 'Learned new facts' : 'Knowledge store updated',
      body: `${`The run's facts went into the local knowledge store${counts ? ` (${counts})` : ''}${names.length ? ` about ${joinList(names.slice(0, 3))}` : ''}`.replace(/\.*$/, '.')} ${learned}`.trim(),
      chips: [
        c?.newEntities ? { label: `+${c.newEntities} ${c.newEntities === 1 ? 'entity' : 'entities'}`, tone: 'ok' as const } : null,
        c?.newRelations ? { label: `+${c.newRelations} ${c.newRelations === 1 ? 'relation' : 'relations'}`, tone: 'ok' as const } : null,
        !isNew && c ? { label: 'already known', tone: 'muted' as const } : null,
      ].filter(Boolean) as ReplayChip[],
      narration: isNew
        ? `Finally, it stored what it learned in the knowledge graph${c!.newRelations ? `: ${spokenCount(c!.newRelations)} new facts` : ''}.`
        : 'Finally, the knowledge store confirmed these facts were already known.',
      tone: isNew ? 'ok' : 'neutral',
      targets: {
        pipeline: { nodes: ['kg-domain'], edges: ['answer->kg-domain'] },
        run: { nodes: [`store:${K}:${i}`], edges: [`e:${K}:store:${i}`] },
      },
    })
  })

  return steps
}

// ── Entities: fact trail ──────────────────────────────────────────────────────

interface FactEntity {
  id: string
  type: string
  name: string
  props?: Record<string, unknown> | null
  mention_count?: number | null
}
interface FactRelation {
  id: string
  src: string
  dst: string
  type: string
  confidence?: number | null
  source?: string | null
  props?: Record<string, unknown> | null
}

const OWNER_ROLE = /\b(eigenaar|eigenares|owner|proprietor|founder|oprichter|directeur-grootaandeelhouder|dga)\b/i

/** "Eenmanszaak (sole proprietorship)" → "sole proprietorship (Eenmanszaak)". */
function legalFormText(v: unknown): string {
  const s = str(v).trim()
  const m = /^(.*?)\s*\((.+)\)$/.exec(s)
  return m ? `${m[2]} (${m[1]})` : s
}

export function buildFactTrail(kg: { entities: FactEntity[]; relations: FactRelation[] } | null | undefined): ReplayStep[] {
  const steps: ReplayStep[] = []
  if (!kg || !Array.isArray(kg.entities) || !Array.isArray(kg.relations)) return steps
  const focal = focalPerson(kg as any) as FactEntity | null
  if (!focal) return steps
  const byId = new Map(kg.entities.filter(e => e && e.id).map(e => [e.id, e]))
  const rels = kg.relations.filter(r => r && byId.has(r.src) && byId.has(r.dst))
  const owner = [focal.name]
  const docNames = friendlyDocNames(
    kg.entities
      .filter(e => e.type === 'document')
      .map(e => ({ id: e.id, filename: str(e.props?.filename) || e.name, hints: { kind: str(e.props?.kind), signatureCard: !!e.props?.signature_card, ownerNames: owner } })),
  )
  const nameOf = (id: string) => {
    const e = byId.get(id)
    if (!e) return ''
    return e.type === 'document' ? docNames.get(id) || friendlyDocName(str(e.props?.filename) || e.name) : e.name
  }
  const first = focal.name.split(/\s+/)[0] || focal.name
  const F = focal.id
  /** Documents an entity is mentioned in (preferring one matching `prefer`). */
  const sourceDoc = (entityId: string, prefer?: RegExp): { doc: string; rel: string } | null => {
    const ms = rels.filter(r => r.type === 'mentioned_in' && r.src === entityId && byId.get(r.dst)?.type === 'document')
    const hit = (prefer && ms.find(r => prefer.test(nameOf(r.dst)))) || ms[0]
    return hit ? { doc: hit.dst, rel: hit.id } : null
  }

  const docCount = new Set(rels.filter(r => byId.get(r.dst)?.type === 'document' || byId.get(r.src)?.type === 'document').flatMap(r => [r.src, r.dst]).filter(id => byId.get(id)?.type === 'document')).size
  const factCount = rels.filter(r => r.type !== 'mentioned_in' && r.type !== 'candidate_profile').length
  steps.push({
    id: 'person',
    kind: 'person',
    eyebrow: 'Knowledge graph',
    title: focal.name,
    body: `Everything Persephone learned about ${focal.name} is stored as facts, each tied to the document or check it came from: ${plural(factCount, 'fact')}${docCount ? ` from ${plural(docCount, 'document')}` : ''} in this scope.`,
    chips: [{ label: plural(factCount, 'fact'), tone: 'info' }, ...(docCount ? [{ label: plural(docCount, 'document'), tone: 'muted' as const }] : [])],
    narration: `This is what Persephone knows about ${focal.name}, and where each fact comes from.`,
    tone: 'neutral',
    targets: { entities: { nodes: [F], edges: [] } },
  })

  const usedRoles = new Set<string>()
  const roles = rels.filter(r => r.type === 'has_role' && r.src === F)

  // Ownership
  for (const r of rels.filter(r => r.type === 'owns' && r.src === F).slice(0, 2)) {
    const org = byId.get(r.dst)!
    const p = { ...(org.props || {}), ...(r.props || {}) } as Record<string, unknown>
    const src = sourceDoc(org.id, /registry/i) || sourceDoc(F, /registry/i)
    const role = roles.find(x => OWNER_ROLE.test(byId.get(x.dst)?.name || ''))
    if (role) usedRoles.add(role.id)
    const since = str(p.registered_since)
    const legal = legalFormText(p.legal_form)
    const registryProps = !!(p.registry_number || p.registered_since || p.legal_form)
    const srcIsRegistry = !!src && /registry/i.test(nameOf(src.doc))
    // Registry details learned from an extract outside this scope: don't credit another document.
    const srcName = src && (srcIsRegistry || !registryProps) ? nameOf(src.doc) : registryProps ? 'company registry extract read earlier' : ''
    const registry = srcIsRegistry || registryProps
    const roleTxt = role ? ` (${byId.get(role.dst)!.name.toLowerCase()})` : ''
    steps.push({
      id: `fact:${r.id}`,
      kind: 'fact',
      eyebrow: registry ? 'Fact · registry' : 'Fact · ownership',
      title: `owns ${org.name}`,
      body: `${srcName ? `${TheDoc(srcName)} states` : 'The documents state'} that ${first} is the owner${roleTxt} of ${org.name}${legal ? `, a ${legal}` : ''}${since ? `, registered since ${since}` : ''}.`,
      chips: [
        { label: 'owns', tone: 'info' },
        ...(registry ? [{ label: 'registry', tone: 'ok' as const }] : []),
        ...(since ? [{ label: `since ${since}`, tone: 'muted' as const }] : []),
      ],
      narration: `${srcName ? `${TheDoc(srcName)} says` : 'The documents say'} ${first} owns ${org.name}${since ? `, since ${since}` : ''}.`,
      tone: registry ? 'ok' : 'neutral',
      relationId: r.id,
      targets: {
        entities: {
          nodes: [org.id, F, ...(src && srcName === nameOf(src.doc) ? [src.doc] : []), ...(role ? [role.dst] : [])],
          edges: [r.id, ...(src && srcName === nameOf(src.doc) ? [src.rel] : []), ...(role ? [role.id] : [])],
        },
      },
    })
  }

  // Employment (+ the role held there)
  for (const r of rels.filter(r => r.type === 'works_at' && r.src === F).slice(0, 2)) {
    const org = byId.get(r.dst)!
    const src = sourceDoc(org.id, /\bcv\b|resume/i)
    const role = roles.find(x => !usedRoles.has(x.id) && !OWNER_ROLE.test(byId.get(x.dst)?.name || ''))
    if (role) usedRoles.add(role.id)
    const srcName = src ? nameOf(src.doc) : ''
    const roleName = role ? byId.get(role.dst)!.name : ''
    steps.push({
      id: `fact:${r.id}`,
      kind: 'fact',
      eyebrow: 'Fact · employment',
      title: `works at ${org.name}`,
      body: `${srcName ? `${TheDoc(srcName)} says` : 'The documents say'} ${first} works at ${org.name}${roleName ? ` as ${roleName}` : ''}. Extracted by the answer model while reading, then stored as a fact.`,
      chips: [{ label: 'works at', tone: 'info' }, ...(roleName ? [{ label: roleName, tone: 'muted' as const }] : [])],
      narration: `${srcName ? `${TheDoc(srcName)} says` : 'The documents say'} ${first} works at ${org.name}${roleName ? ` as ${roleName}` : ''}.`,
      tone: 'neutral',
      relationId: r.id,
      targets: {
        entities: {
          nodes: [org.id, F, ...(src ? [src.doc] : []), ...(role ? [role.dst] : [])],
          edges: [r.id, ...(src ? [src.rel] : []), ...(role ? [role.id] : [])],
        },
      },
    })
  }

  // Remaining roles
  for (const r of roles.filter(r => !usedRoles.has(r.id)).slice(0, 2)) {
    const raw = byId.get(r.dst)!.name
    const roleName = /^eigena(ar|res)$/i.test(raw) ? `${raw.toLowerCase()} (owner)` : raw
    steps.push({
      id: `fact:${r.id}`,
      kind: 'fact',
      eyebrow: 'Fact · role',
      title: `is ${roleName}`,
      body: `The documents describe ${first} as ${roleName}.`,
      chips: [{ label: 'has role', tone: 'info' }],
      narration: `The documents describe ${first} as ${roleName}.`,
      tone: 'neutral',
      relationId: r.id,
      targets: { entities: { nodes: [r.dst, F], edges: [r.id] } },
    })
  }

  // Signature specimens
  for (const r of rels.filter(r => r.type === 'signature_specimen' && r.dst === F).slice(0, 1)) {
    const card = nameOf(r.src)
    const n = num(r.props?.n_references)
    steps.push({
      id: `fact:${r.id}`,
      kind: 'fact',
      eyebrow: 'Fact · specimens',
      title: `${card}: reference signatures`,
      body: `${TheDoc(card)} holds ${n ? plural(n, 'reference signature') : 'reference signatures'} of ${first} — the specimens the signature engine compares against.`,
      chips: [{ label: 'signature specimen', tone: 'info' }, ...(n ? [{ label: plural(n, 'reference'), tone: 'muted' as const }] : [])],
      narration: `${TheDoc(card)} holds ${n ? spokenCount(n) : ''} reference signatures of ${first}.`.replace(/\s+/g, ' '),
      tone: 'info',
      relationId: r.id,
      targets: { entities: { nodes: [r.src, F], edges: [r.id] } },
    })
  }

  // Signed letters (and verification against the card)
  const covered = new Set<string>()
  for (const r of rels.filter(r => r.type === 'signed' && r.src === F).slice(0, 2)) {
    const letter = nameOf(r.dst)
    const p = r.props || {}
    const score = num(p.score) ?? (num(r.confidence) != null ? Math.round(r.confidence! * 100) : null)
    const band = str(p.band)
    const n = num(p.n_references)
    const va = rels.find(x => x.type === 'verified_against' && x.src === r.dst)
    if (va) covered.add(va.id)
    const card = va ? nameOf(va.dst) : ''
    const tone: ReplayTone = band === 'inconclusive' ? 'warn' : band === 'inconsistent' ? 'bad' : 'ok'
    steps.push({
      id: `fact:${r.id}`,
      kind: 'fact',
      eyebrow: 'Fact · signature',
      title: `signed ${letter}`,
      body: `The signature on ${theDoc(letter)} matches ${first}'s reference signatures${card ? ` from ${theDoc(card)}` : ''}: ${score != null ? `${score}%` : 'match'}${band ? `, ${band}` : ''}${n ? ` with ${plural(n, 'reference')}` : ''}. Measured by the local signature engine, not decided by an LLM.`,
      chips: [
        ...(score != null ? [{ label: `${score}%`, tone: (tone === 'ok' ? 'ok' : tone === 'warn' ? 'warn' : 'bad') as ReplayChip['tone'] }] : []),
        ...(band ? [{ label: band, tone: (tone === 'ok' ? 'ok' : tone === 'warn' ? 'warn' : 'bad') as ReplayChip['tone'] }] : []),
        { label: 'signature engine', tone: 'info' },
      ],
      narration: `The signature on ${theDoc(letter)} matches ${first}'s references${score != null ? `: ${score} percent` : ''}${band ? `, ${band}` : ''}.`,
      tone,
      relationId: r.id,
      targets: {
        entities: { nodes: [r.dst, F, ...(va ? [va.dst] : [])], edges: [r.id, ...(va ? [va.id] : [])] },
      },
    })
  }
  // Verifications that did not become "signed" (e.g. a forged signature)
  const focalKey = foldText(focal.name).trim()
  for (const r of rels.filter(r => r.type === 'verified_against' && !covered.has(r.id))) {
    const p = r.props || {}
    const person = str(p.person)
    if (person && foldText(person).trim() !== focalKey) continue
    const band = str(p.band)
    const score = num(p.score)
    const letter = nameOf(r.src)
    const card = nameOf(r.dst)
    const bad = band === 'inconsistent'
    steps.push({
      id: `fact:${r.id}`,
      kind: 'fact',
      eyebrow: 'Fact · signature',
      title: bad ? `signature does not match` : `signature ${band || 'checked'}`,
      body: bad
        ? `The signature on ${theDoc(letter)} does not match ${first}'s reference signatures from ${theDoc(card)}: ${score != null ? `${score}%, ` : ''}inconsistent. So Persephone did not store that ${first} signed it.`
        : `The signature on ${theDoc(letter)} was compared with ${theDoc(card)}: ${score != null ? `${score}%, ` : ''}${band || 'checked'}.`,
      chips: [
        ...(score != null ? [{ label: `${score}%`, tone: (bad ? 'bad' : 'warn') as ReplayChip['tone'] }] : []),
        ...(band ? [{ label: band, tone: (bad ? 'bad' : 'warn') as ReplayChip['tone'] }] : []),
        { label: 'signature engine', tone: 'info' },
      ],
      narration: bad
        ? `The signature on ${theDoc(letter)} does not match ${first}'s references${score != null ? `: only ${score} percent` : ''}. So no signed fact was stored.`
        : `The signature on ${theDoc(letter)} was ${band || 'checked'}.`,
      tone: bad ? 'bad' : 'warn',
      relationId: r.id,
      targets: { entities: { nodes: [r.src, r.dst, F], edges: [r.id] } },
    })
  }

  // Online profiles (one likely match per platform)
  const platforms = new Set<string>()
  for (const r of rels.filter(r => r.type === 'likely_profile' && r.src === F)) {
    const prof = byId.get(r.dst)!
    const platform = resolvePlatform(prof.props?.platform, str(prof.props?.url))
    const label = platform ? SOCIAL_PLATFORMS[platform].label : 'Web'
    if (platforms.has(label)) continue
    platforms.add(label)
    const title = clip(prof.name.replace(/\s*[|\-–—·•/]\s*(?:LinkedIn|Facebook|Instagram|Twitter|X)\s*$/i, ''), 70)
    steps.push({
      id: `fact:${r.id}`,
      kind: 'fact',
      eyebrow: 'Fact · web lookup',
      title: `${label}: likely match`,
      body: `A web lookup found the ${label} profile “${title}” and judged it a likely match for ${first}. Only the name and role left this machine for that search.`,
      chips: [{ label, tone: 'info' }, { label: 'likely match', tone: 'ok' }, ...(num(r.confidence) != null ? [{ label: pct(r.confidence), tone: 'muted' as const }] : [])],
      narration: `A web lookup found a ${label} profile that is likely ${first}.`,
      tone: 'ok',
      relationId: r.id,
      targets: { entities: { nodes: [r.dst, F], edges: [r.id] } },
    })
  }

  return steps.length > 1 ? steps : []
}
