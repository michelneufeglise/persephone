/**
 * Default "Main Model" selection for the setup wizard.
 *
 * Pure helpers (no React, no fetch) so they can be unit-checked in isolation.
 *
 *  - `isGeneralChatModel(name, info?)` — true when an installed Ollama model is
 *    a sensible general-purpose chat model (not OCR / embeddings / TTS / speech /
 *    rerank / Laya, not a code specialist, not a vision-only family, not tiny).
 *  - `pickDefaultChatModel({ installed, recommendations, current })` — chooses
 *    the model the wizard pre-fills: the saved active model if it is still
 *    installed, else the best installed chat recommendation (ModelStep order),
 *    else the largest installed general chat model, else ''.
 */

/** Shape of one entry in Ollama's /api/tags (proxied by GET /api/models). */
export interface InstalledModelInfo {
  name: string
  size?: number
  details?: {
    parameter_size?: string
    family?: string
    families?: string[] | null
  }
  /** Newer Ollama builds may report capabilities ("completion", "embedding", "vision", …). */
  capabilities?: string[]
}

/** Subset of a /api/setup/optimized-models chat entry used for ranking. */
export interface ChatRecommendation {
  id: string
  tok_per_s_est?: number
  fit?: string
  ram_min_gb?: number
}

/** Models at or below this size (billions of parameters) are too small for main chat. */
export const MIN_CHAT_PARAMS_B = 1.5

// Models that cannot hold a conversation at all.
const NON_CHAT_NAME = [
  /embed/, /ocr/, /orpheus/, /kokoro/, /whisper/, /rerank/, /laya/,
  /(^|[^a-z])tts([^a-z]|$)/,
]
const NON_CHAT_FAMILIES = new Set([
  'bert', 'nomic-bert', 'xlm-roberta', 'deepseek2-ocr', 'glmocr',
])

// Code specialists (Ornith is the in-app agentic-coder persona, catalog category "code").
const CODE_NAME = [
  /coder/, /codellama/, /codegemma/, /codestral/, /codeqwen/, /starcoder/, /ornith/,
]

// Vision-only families. Multimodal *general* models (gemma3/gemma4, llama4, …) are fine.
const VISION_NAME = [
  /llava/,                 // llava, bakllava, llava-llama3, …
  /moondream/,
  /minicpm-[vo]/,          // minicpm-v, minicpm-o2.6
  /qwen[\d.]*-?vl/,        // qwen2.5vl, qwen2-vl, qwen3-vl
  /-vision/,               // llama3.2-vision, granite3.2-vision
]
const VISION_FAMILIES = new Set(['clip', 'mllama', 'qwen2vl', 'qwen25vl', 'qwen3vl'])

function familiesOf(info?: InstalledModelInfo): string[] {
  const d = info?.details
  if (!d) return []
  const out = [...(d.families ?? [])]
  if (d.family) out.push(d.family)
  return out.map(f => f.toLowerCase())
}

/** Parse "7.6B" / "494.03M" / "1.5b" → billions. */
function parseSizeToken(num: string, unit: string): number {
  const n = parseFloat(num)
  if (!isFinite(n)) return NaN
  return unit.toLowerCase() === 'm' ? n / 1000 : n
}

/**
 * Parameter count in billions, from Ollama details.parameter_size when given,
 * else from the name (":1.5b", ":0.5b", "-27b", "35b-a3b" → 35). Null if unknown.
 */
export function parseParamsB(name: string, info?: InstalledModelInfo): number | null {
  const ps = info?.details?.parameter_size
  if (ps) {
    const m = ps.trim().match(/^(\d+(?:\.\d+)?)\s*([bm])$/i)
    if (m) {
      const v = parseSizeToken(m[1], m[2])
      if (isFinite(v)) return v
    }
  }
  // A size token must not be glued to a preceding letter/digit (rules out the
  // "a3b" active-expert suffix and "e4b"), nor followed by another letter.
  const m = name.toLowerCase().match(/(?:^|[^a-z0-9.])(\d+(?:\.\d+)?)([bm])(?![a-z])/)
  if (m) {
    const v = parseSizeToken(m[1], m[2])
    if (isFinite(v)) return v
  }
  return null
}

/** True for models that can't chat at all (OCR, embeddings, TTS, speech, rerank, Laya). */
export function isNonChatModel(name: string, info?: InstalledModelInfo): boolean {
  const lower = name.toLowerCase()
  if (NON_CHAT_NAME.some(re => re.test(lower))) return true
  if (familiesOf(info).some(f => NON_CHAT_FAMILIES.has(f))) return true
  const caps = info?.capabilities
  if (Array.isArray(caps) && caps.length > 0 && !caps.includes('completion')) return true
  return false
}

/** True when the model is a sensible general-purpose main chat model. */
export function isGeneralChatModel(name: string, info?: InstalledModelInfo): boolean {
  if (!name) return false
  const lower = name.toLowerCase()
  if (isNonChatModel(name, info)) return false
  if (CODE_NAME.some(re => re.test(lower))) return false
  if (VISION_NAME.some(re => re.test(lower))) return false
  if (familiesOf(info).some(f => VISION_FAMILIES.has(f))) return false
  const params = parseParamsB(name, info)
  if (params !== null && params <= MIN_CHAT_PARAMS_B) return false
  return true
}

/** Lower-case and drop an implicit ":latest" so "minicpm-v" == "minicpm-v:latest". */
function normalizeId(id: string): string {
  const l = id.trim().toLowerCase()
  return l.endsWith(':latest') ? l.slice(0, -':latest'.length) : l
}

/**
 * Exact Ollama model-name match, treating a missing tag as ":latest"
 * ("minicpm-v" == "minicpm-v:latest"; "llama3.2:3b" != "llama3.2-vision:latest";
 * "deepseek-r1:7b" != "deepseek-r1:14b").
 */
export function sameModelId(a: string, b: string): boolean {
  if (!a || !b) return false
  return normalizeId(a) === normalizeId(b)
}

function toInfo(m: string | InstalledModelInfo): InstalledModelInfo {
  return typeof m === 'string' ? { name: m } : m
}

const HIDDEN_FITS = new Set(['slow', 'unsupported'])

export function pickDefaultChatModel(opts: {
  installed: Array<string | InstalledModelInfo>
  recommendations?: ChatRecommendation[] | null
  current?: string | null
}): string {
  const installed = (opts.installed ?? []).map(toInfo).filter(m => m && m.name)
  if (installed.length === 0) return ''
  const byId = new Map<string, InstalledModelInfo>()
  for (const m of installed) {
    const k = normalizeId(m.name)
    if (!byId.has(k)) byId.set(k, m)
  }

  // 1. Keep the saved main model when it is still installed (and can chat at all).
  const current = (opts.current ?? '').trim()
  if (current) {
    const hit = byId.get(normalizeId(current))
    if (hit && !isNonChatModel(hit.name, hit)) return hit.name
  }

  // 2. Best *actually installed* chat recommendation, in ModelStep's order:
  //    shown-by-default fits first (ModelStep hides slow/unsupported), then
  //    tok/s desc, then RAM asc. Unsupported (can't run) is never auto-picked.
  const recs = (opts.recommendations ?? [])
    .filter(r => r && r.id && r.fit !== 'unsupported')
    .map(r => ({ rec: r, info: byId.get(normalizeId(r.id)) }))
    .filter((x): x is { rec: ChatRecommendation; info: InstalledModelInfo } =>
      !!x.info && isGeneralChatModel(x.info.name, x.info))
  recs.sort((a, b) => {
    const ah = HIDDEN_FITS.has(a.rec.fit ?? '') ? 1 : 0
    const bh = HIDDEN_FITS.has(b.rec.fit ?? '') ? 1 : 0
    if (ah !== bh) return ah - bh
    const at = a.rec.tok_per_s_est ?? -1
    const bt = b.rec.tok_per_s_est ?? -1
    if (at !== bt) return bt - at
    return (a.rec.ram_min_gb ?? 0) - (b.rec.ram_min_gb ?? 0)
  })
  if (recs.length > 0) return recs[0].info.name

  // 3. Largest installed general chat model (parameter count, then bytes on disk).
  const general = installed.filter(m => isGeneralChatModel(m.name, m))
  if (general.length === 0) return ''
  general.sort((a, b) => {
    const ap = parseParamsB(a.name, a) ?? -1
    const bp = parseParamsB(b.name, b) ?? -1
    if (ap !== bp) return bp - ap
    return (b.size ?? 0) - (a.size ?? 0)
  })
  return general[0].name
}
