/**
 * Curated metadata for common Ollama model families.
 *
 * The catalog is matched by *family prefix* (case-insensitive) against the
 * Ollama tag name (e.g. "qwen2.5:7b" → "qwen2.5"). Per-tag overrides can be
 * added to the OVERRIDES map below for specific sizes / variants.
 *
 * Use `resolveModelMeta(name)` from the UI to get a friendly description for
 * a given Ollama model tag.
 */

export type ModelType =
  | 'dense'        // standard transformer (most LLMs)
  | 'moe'          // mixture-of-experts
  | 'vision'       // multimodal vision-language model
  | 'embedding'    // sentence embedding model
  | 'tts'          // text-to-speech
  | 'ocr'          // OCR / document understanding

export interface ModelMeta {
  family:           string
  displayName:      string
  type:             ModelType
  paramsLabel?:     string     // "7B", "32B", "30B-A3B" (active for MoE)
  contextK?:        number     // context window in K tokens
  releasedYear?:    number
  vendor?:          string
  license?:         string
  supportsTools?:   boolean
  supportsVision?:  boolean
  supportsThinking?:boolean
  strengths:        string[]   // 2-4 short bullets
  bestFor?:         string     // one-liner: "best when X"
  tagline?:         string     // 6-10 word headline
}

/* ─────────────────────────────────────────────────────────────────
   Family catalog (matched by tag prefix; longest match wins)
   ────────────────────────────────────────────────────────────────── */
const FAMILIES: Record<string, ModelMeta> = {
  // ── Alibaba Qwen line ──
  'qwen2.5-coder': {
    family: 'qwen2.5-coder',
    displayName: 'Qwen 2.5 Coder',
    type: 'dense',
    contextK: 128,
    releasedYear: 2024,
    vendor: 'Alibaba',
    license: 'Apache-2.0 / Qwen',
    supportsTools: true,
    strengths: ['Code generation & repo-scale completion', 'Fill-in-the-middle', 'Refactoring & explanation'],
    bestFor: 'Writing or editing code in any major language.',
    tagline: 'Specialist coding model from the Qwen family.',
  },
  'qwen2.5vl': {
    family: 'qwen2.5vl',
    displayName: 'Qwen 2.5 VL',
    type: 'vision',
    contextK: 128,
    releasedYear: 2025,
    vendor: 'Alibaba',
    license: 'Qwen',
    supportsTools: true,
    supportsVision: true,
    strengths: ['Image understanding & captioning', 'Document & chart OCR', 'Visual reasoning'],
    bestFor: 'Asking questions about screenshots, PDFs, or photos.',
    tagline: 'Multimodal vision-language Qwen.',
  },
  'qwen2.5': {
    family: 'qwen2.5',
    displayName: 'Qwen 2.5',
    type: 'dense',
    contextK: 128,
    releasedYear: 2024,
    vendor: 'Alibaba',
    license: 'Apache-2.0 / Qwen',
    supportsTools: true,
    strengths: ['Strong general reasoning', 'Reliable tool / function calling', 'Multilingual (30+ languages)'],
    bestFor: 'General chat, agents, and tool orchestration.',
    tagline: 'Workhorse open model — tools, reasoning, multilingual.',
  },
  'qwen3.6': {
    family: 'qwen3.6',
    displayName: 'Qwen 3.6',
    type: 'moe',
    contextK: 256,
    releasedYear: 2026,
    vendor: 'Alibaba',
    license: 'Apache-2.0 / Qwen',
    supportsTools: true,
    supportsThinking: true,
    strengths: ['Native thinking mode', 'MoE — fast at large param counts', 'Long-context retrieval'],
    bestFor: 'Complex multi-step reasoning where you want chain-of-thought.',
    tagline: 'MoE reasoning model with visible thinking.',
  },
  'qwen3': {
    family: 'qwen3',
    displayName: 'Qwen 3',
    type: 'dense',
    contextK: 128,
    releasedYear: 2025,
    vendor: 'Alibaba',
    license: 'Apache-2.0 / Qwen',
    supportsTools: true,
    supportsThinking: true,
    strengths: ['Built-in /think mode (toggleable)', 'Solid coding + math', 'Tool calling'],
    bestFor: 'Reasoning-heavy chat with visible thought process.',
    tagline: 'Reasoning Qwen — switchable thinking mode.',
  },

  // ── Google Gemma line ──
  'gemma4': {
    family: 'gemma4',
    displayName: 'Gemma 4',
    type: 'dense',
    contextK: 128,
    releasedYear: 2026,
    vendor: 'Google',
    license: 'Gemma',
    supportsTools: true,
    supportsThinking: true,
    strengths: ['Strong on summarisation + writing', 'Polite, balanced tone', 'Native thinking traces'],
    bestFor: 'Drafting prose, summarising long inputs, polite dialogue.',
    tagline: 'Polished writer with native chain-of-thought.',
  },
  'gemma3': {
    family: 'gemma3',
    displayName: 'Gemma 3',
    type: 'dense',
    contextK: 128,
    releasedYear: 2025,
    vendor: 'Google',
    license: 'Gemma',
    strengths: ['Concise writing', 'Multilingual', 'Lightweight'],
    bestFor: 'Casual chat and quick summaries.',
    tagline: 'Compact polished writer.',
  },

  // ── Meta Llama line ──
  'llama3.3': {
    family: 'llama3.3',
    displayName: 'Llama 3.3',
    type: 'dense',
    paramsLabel: '70B',
    contextK: 128,
    releasedYear: 2024,
    vendor: 'Meta',
    license: 'Llama 3.3 Community',
    supportsTools: true,
    strengths: ['Frontier-class general reasoning', 'Strong tool use', 'Excellent instruction following'],
    bestFor: 'Deep questions where you want the best open-weights answer.',
    tagline: 'Meta\'s 70B flagship — slow but smart.',
  },
  'llama3.2-vision': {
    family: 'llama3.2-vision',
    displayName: 'Llama 3.2 Vision',
    type: 'vision',
    contextK: 128,
    releasedYear: 2024,
    vendor: 'Meta',
    license: 'Llama 3.2 Community',
    supportsVision: true,
    strengths: ['Image captioning + Q&A', 'Diagram & chart reading', 'Solid text fallback'],
    bestFor: 'Photo + text mixed conversations.',
    tagline: 'Vision-enabled Llama.',
  },
  'llama3.2': {
    family: 'llama3.2',
    displayName: 'Llama 3.2',
    type: 'dense',
    contextK: 128,
    releasedYear: 2024,
    vendor: 'Meta',
    license: 'Llama 3.2 Community',
    supportsTools: true,
    strengths: ['Compact + fast', 'Good general chat', 'Tool calling'],
    bestFor: 'Snappy local responses on modest hardware.',
    tagline: 'Small, fast, capable Llama.',
  },
  'llama3.1': {
    family: 'llama3.1',
    displayName: 'Llama 3.1',
    type: 'dense',
    contextK: 128,
    releasedYear: 2024,
    vendor: 'Meta',
    license: 'Llama 3.1 Community',
    supportsTools: true,
    strengths: ['Solid generalist', 'Tool calling', 'Long context'],
    bestFor: 'Backbone for agents.',
    tagline: 'Reliable agentic Llama.',
  },

  // ── Nvidia Nemotron ──
  'nemotron-3-nano': {
    family: 'nemotron-3-nano',
    displayName: 'Nemotron 3 Nano',
    type: 'moe',
    paramsLabel: '30B-A3B',
    contextK: 256,
    releasedYear: 2026,
    vendor: 'NVIDIA',
    license: 'NVIDIA OpenModel',
    supportsTools: true,
    supportsThinking: true,
    strengths: ['MoE — 30B params, 3B active per token', 'Built for reasoning + retrieval', 'Excellent throughput on a single GPU'],
    bestFor: 'Fast reasoning when you have a beefy GPU but limited VRAM.',
    tagline: 'Efficient MoE reasoner from NVIDIA.',
  },
  'nemotron': {
    family: 'nemotron',
    displayName: 'Nemotron',
    type: 'dense',
    contextK: 128,
    releasedYear: 2025,
    vendor: 'NVIDIA',
    license: 'NVIDIA OpenModel',
    supportsTools: true,
    strengths: ['Tuned for assistant tasks', 'RAG-friendly', 'Tool use'],
    tagline: 'NVIDIA\'s assistant-tuned model.',
    bestFor: 'Enterprise-style chat with retrieval.',
  },

  // ── Mistral / Mixtral ──
  'mixtral': {
    family: 'mixtral',
    displayName: 'Mixtral',
    type: 'moe',
    paramsLabel: '8×7B / 8×22B',
    contextK: 64,
    releasedYear: 2024,
    vendor: 'Mistral',
    license: 'Apache-2.0',
    supportsTools: true,
    strengths: ['Sparse MoE — high throughput', 'Multilingual', 'Concise European prose'],
    bestFor: 'Speed-sensitive chat in French/German/Spanish.',
    tagline: 'Open MoE from Mistral.',
  },
  'mistral-nemo': {
    family: 'mistral-nemo',
    displayName: 'Mistral Nemo',
    type: 'dense',
    paramsLabel: '12B',
    contextK: 128,
    releasedYear: 2024,
    vendor: 'Mistral × NVIDIA',
    license: 'Apache-2.0',
    supportsTools: true,
    strengths: ['Strong tool calling', 'Long context', 'Good multilingual'],
    tagline: 'Mistral × NVIDIA collab.',
  },
  'mistral': {
    family: 'mistral',
    displayName: 'Mistral',
    type: 'dense',
    contextK: 32,
    releasedYear: 2024,
    vendor: 'Mistral',
    license: 'Apache-2.0',
    supportsTools: true,
    strengths: ['Compact + fast', 'European-tuned multilingual', 'Tool calling'],
    tagline: 'Lean European generalist.',
  },

  // ── Deepseek ──
  'deepseek-r1': {
    family: 'deepseek-r1',
    displayName: 'DeepSeek R1',
    type: 'dense',
    contextK: 128,
    releasedYear: 2025,
    vendor: 'DeepSeek',
    license: 'MIT',
    supportsThinking: true,
    strengths: ['Reasoning-first architecture', 'Visible <think> chain', 'Math & logic'],
    bestFor: 'Hard reasoning problems where you want to see the chain of thought.',
    tagline: 'Reasoning specialist — visible thinking.',
  },
  'deepseek': {
    family: 'deepseek',
    displayName: 'DeepSeek',
    type: 'dense',
    contextK: 128,
    releasedYear: 2024,
    vendor: 'DeepSeek',
    license: 'MIT',
    supportsTools: true,
    strengths: ['Strong code generation', 'Long context', 'Cheap to run'],
    tagline: 'Efficient open chat from DeepSeek.',
  },

  // ── Ornith (Qwen3-based agentic coder) ──
  'ornith': {
    family: 'ornith',
    displayName: 'Ornith',
    type: 'dense',
    paramsLabel: '9B',
    contextK: 262,
    releasedYear: 2026,
    vendor: 'Community / Qwen3',
    license: 'MIT',
    supportsTools: true,
    supportsThinking: true,
    strengths: ['Agentic coder — 262K context', 'Native tools + thinking', 'Small enough to run fast'],
    bestFor: 'Terminal-style coding assistant with tool use.',
    tagline: 'Persephone\'s terminal coder.',
  },

  // ── Misc ──
  'hermes3': {
    family: 'hermes3',
    displayName: 'Hermes 3',
    type: 'dense',
    contextK: 128,
    releasedYear: 2024,
    vendor: 'Nous Research',
    license: 'Apache-2.0 (from Llama)',
    supportsTools: true,
    strengths: ['Excellent at role-play / persona', 'Tool calling', 'JSON output'],
    bestFor: 'Personas, structured output, agentic flows.',
    tagline: 'Persona-tuned Llama by Nous.',
  },
  'gpt-oss': {
    family: 'gpt-oss',
    displayName: 'GPT-OSS',
    type: 'dense',
    contextK: 128,
    releasedYear: 2025,
    vendor: 'OpenAI (open weights)',
    license: 'Apache-2.0',
    supportsTools: true,
    supportsThinking: true,
    strengths: ['OpenAI-flavoured behaviour', 'Thinking mode', 'Tool calling'],
    tagline: 'OpenAI\'s open-weights line.',
  },

  // ── Vision / OCR / Embeddings (filtered out of chat dropdown elsewhere) ──
  'glm-ocr': {
    family: 'glm-ocr',
    displayName: 'GLM OCR',
    type: 'ocr',
    vendor: 'Zhipu / Tsinghua',
    strengths: ['Document OCR', 'Layout-aware extraction'],
    tagline: 'OCR specialist.',
  },
  'openbmb/minicpm-o': {
    family: 'openbmb/minicpm-o',
    displayName: 'MiniCPM-O',
    type: 'vision',
    vendor: 'OpenBMB',
    license: 'OpenBMB',
    supportsVision: true,
    strengths: ['Tiny multimodal', 'Image + audio understanding', 'Edge-friendly'],
    tagline: 'Edge multimodal.',
  },
  'mxbai-embed': {
    family: 'mxbai-embed',
    displayName: 'MixedBread Embed',
    type: 'embedding',
    vendor: 'Mixedbread',
    license: 'Apache-2.0',
    strengths: ['High-quality semantic search', '1024-dim vectors', 'Multilingual'],
    tagline: 'Sentence embeddings.',
  },
  'orpheus': {
    family: 'orpheus',
    displayName: 'Orpheus TTS',
    type: 'tts',
    vendor: 'Canopy Labs',
    strengths: ['Expressive natural voices', 'SNAC neural codec', '24kHz output'],
    tagline: 'Voice synthesis backend.',
  },
}

/* Optional per-tag overrides (e.g. unusual community quants) */
const OVERRIDES: Record<string, Partial<ModelMeta>> = {
  'qwen3:ohm':  { tagline: 'Community Qwen3 variant — Ohm fine-tune.' },
  'laguna-xs.2:latest': {
    family: 'laguna-xs.2', displayName: 'Laguna XS', type: 'dense',
    vendor: 'Community', strengths: ['Compact community model'], tagline: 'Indie compact LLM.',
  },
}

/* ─── helpers ────────────────────────────────────────────────────── */

const FAMILY_KEYS = Object.keys(FAMILIES).sort((a, b) => b.length - a.length)

/** Return a ModelMeta for an Ollama tag like "qwen2.5:7b" or "llama3.3:70b". */
export function resolveModelMeta(name: string): ModelMeta {
  const lower = name.toLowerCase()

  // 1. exact-tag override
  if (OVERRIDES[name] || OVERRIDES[lower]) {
    const base = matchFamily(lower) ?? UNKNOWN(name)
    return { ...base, ...(OVERRIDES[name] ?? OVERRIDES[lower]!), ...derive(lower) }
  }

  const family = matchFamily(lower)
  if (family) return { ...family, ...derive(lower) }
  return UNKNOWN(name)
}

function matchFamily(lower: string): ModelMeta | null {
  for (const key of FAMILY_KEYS) {
    if (lower.startsWith(key)) return FAMILIES[key]
  }
  return null
}

/** Extract param size + quantisation hints from the tag suffix. */
function derive(lower: string): Partial<ModelMeta> {
  const colon = lower.indexOf(':')
  const tag   = colon >= 0 ? lower.slice(colon + 1) : ''
  const out: Partial<ModelMeta> = {}

  // params:  "7b", "12b", "70b", "30b-a3b" (MoE active params)
  const m = tag.match(/(\d+(?:\.\d+)?)\s*b(?:-a(\d+(?:\.\d+)?)b)?/i)
  if (m) {
    out.paramsLabel = m[2]
      ? `${m[1]}B-A${m[2]}B`        // MoE: total-A active
      : `${m[1]}B`
  }
  return out
}

function UNKNOWN(name: string): ModelMeta {
  return {
    family:      name.split(':')[0],
    displayName: name,
    type:        'dense',
    strengths:   ['Unrecognised model — see Ollama for details.'],
    tagline:     'Unknown model family.',
  }
}

/** Should this model show up in the chat-window dropdown? */
export function isChatModel(meta: ModelMeta): boolean {
  return meta.type !== 'embedding' && meta.type !== 'tts' && meta.type !== 'ocr'
}

/** Human-friendly label for the ModelType chip. */
export function typeLabel(type: ModelType): string {
  switch (type) {
    case 'dense':     return 'LLM'
    case 'moe':       return 'MoE'
    case 'vision':    return 'Vision'
    case 'embedding': return 'Embed'
    case 'tts':       return 'TTS'
    case 'ocr':       return 'OCR'
  }
}
