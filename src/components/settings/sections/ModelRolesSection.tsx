import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Loader2, Boxes, AlertCircle } from 'lucide-react'
import { Panel } from '@/components/ui/Panel'
import { Select } from '@/components/ui/Select'
import { Button } from '@/components/ui/Button'
import { useAppStore } from '@/store/appStore'
import { fetchModels } from '@/lib/ollama'
import { layaStatus } from '@/lib/idp'
import { LayaStatusCard } from '@/components/laya/LayaStatusCard'
import { clsx } from 'clsx'

const ROLES = [
  {
    key: 'active_model', label: 'Main Chat', required: true,
    description: 'The primary model Persephone uses for conversation and reasoning.',
  },
  {
    key: 'judge_model', label: 'Auto-router Judge', required: false,
    description: 'Tiny model that classifies each message so auto-route picks the right chat model. Smaller = faster.',
  },
  {
    key: 'vision_model', label: 'Vision', required: false,
    description: 'Analyses images, screenshots, and visual documents.',
  },
  {
    key: 'code_model', label: 'Code', required: false,
    description: 'Specialised for programming assistance.',
  },
  {
    key: 'ocr_model', label: 'OCR — Text Extraction', required: false,
    description: 'Extracts text from scans, screenshots, and photos of documents.',
  },
  {
    key: 'docs_model', label: 'Documents & PDF', required: false,
    description: 'Reads and reasons about PDFs, contracts, and multi-page documents.',
  },
  {
    key: 'handwriting_model', label: 'Handwriting', required: false,
    description: 'Reads (transcribes) handwritten letters, notes and cursive in the Documents agent — line breaks, names and numbers kept, unreadable words marked [?].',
  },
  {
    key: 'signature_model', label: 'Signature Verification', required: false,
    description: 'Vision model that picks which handwritten line is the signature and explains similarities / differences with the reference signatures. The score itself comes from a local, deterministic engine. Needs a vision-capable model (e.g. gemma4:12b, qwen2.5vl, minicpm-v) — not an OCR-only model. Empty = Handwriting → Vision model.',
  },
  {
    key: 'tables_model', label: 'Spreadsheets & Tables', required: false,
    description: 'Extracts tables and writes spreadsheet formulas.',
  },
  {
    key: 'multidoc_model', label: 'Multi-Document Query', required: false,
    description: 'Reasons across several selected documents at once (RAG). Used by the Documents panel\'s multi-doc chat. Falls back to Documents/Main Chat.',
  },
  {
    key: 'web_lookup_model', label: 'Web Lookup (tools)', required: false,
    description: 'Tool-calling model the Documents agent uses to search the web / LinkedIn when you ask it to verify a person online. Needs tool support — small instruct models work best (e.g. qwen3:4b-instruct-2507, qwen2.5:7b-instruct). Empty = auto-select.',
  },
  {
    key: 'embed_model', label: 'Embeddings', required: false,
    description: 'Powers semantic search, document RAG, and memory. Use a dedicated embedding model (e.g. mxbai-embed-large, nomic-embed-text, bge-m3).',
  },
  {
    key: 'ableton_composer_model', label: 'Ableton Composer', required: false,
    description: 'The standard model for the Ableton track-first composer + edit chat. Default: qwen3.6:35b-a3b.',
  },
  {
    key: 'ableton_deep_model', label: 'Ableton Deep Reasoning', required: false,
    description: 'Used when the composer\'s Deep Reasoning toggle is on. Default: gemma4:26b.',
  },
] as const

type RoleKey = (typeof ROLES)[number]['key']
type RoleValues = Record<RoleKey, string>

const EMPTY_ROLES: RoleValues = {
  active_model: '', judge_model: '', vision_model: '', code_model: '',
  ocr_model: '', docs_model: '', handwriting_model: '', signature_model: '', tables_model: '',
  multidoc_model: '', web_lookup_model: '', embed_model: '',
  ableton_composer_model: '', ableton_deep_model: '',
}

export function ModelRolesSection() {
  const { models, setModels, updateSettings } = useAppStore()
  const [roles, setRoles]         = useState<RoleValues>(EMPTY_ROLES)
  const [loading, setLoading]     = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [savingKey, setSavingKey] = useState<RoleKey | null>(null)
  const [error, setError]         = useState('')
  const [layaAvailable, setLayaAvailable] = useState(false)
  // LLM judge behind Laya (used when Laya is unsure / for LLM-only tasks).
  const [judgeFallback, setJudgeFallback] = useState('')
  const [savingFallback, setSavingFallback] = useState(false)

  const loadRoles = useCallback(async () => {
    const r = await fetch('/api/models/roles')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const data = await r.json()
    setRoles({ ...EMPTY_ROLES, ...data })
    setJudgeFallback(data.judge_fallback_model ?? '')
  }, [])

  const loadLayaStatus = useCallback(async () => {
    try {
      const status = await layaStatus()
      setLayaAvailable(status.available)
    } catch {
      setLayaAvailable(false)
    }
  }, [])

  const refresh = useCallback(async (showSpinner: boolean) => {
    if (showSpinner) setRefreshing(true)
    setError('')
    try {
      const [list] = await Promise.all([fetchModels(), loadRoles(), loadLayaStatus()])
      setModels(list)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      if (showSpinner) setRefreshing(false)
      setLoading(false)
    }
  }, [loadRoles, loadLayaStatus, setModels])

  useEffect(() => { refresh(false) }, [refresh])

  const installedNames = models
    .map(m => m.name)
    .filter(n => !n.toLowerCase().includes('embed'))
    .sort((a, b) => a.localeCompare(b))

  const embedNames = models
    .map(m => m.name)
    .filter(n => n.toLowerCase().includes('embed'))
    .sort((a, b) => a.localeCompare(b))

  function optionsFor(roleKey: RoleKey, current: string) {
    const source = roleKey === 'embed_model' ? embedNames : installedNames
    const opts = source.map(n => ({ value: n, label: n }))

    // Add Laya option for judge_model
    if (roleKey === 'judge_model') {
      if (layaAvailable) {
        opts.unshift({ value: 'laya-builtin', label: 'Laya (built-in · experimental)' })
      }
    }

    if (current && !source.includes(current) && current !== 'laya-builtin') {
      opts.unshift({ value: current, label: `${current} (not installed)` })
    }
    const required = ROLES.find(r => r.key === roleKey)?.required
    if (!required) {
      opts.unshift({ value: '', label: 'None — fall back automatically' })
    }
    return opts
  }

  async function assign(key: RoleKey, value: string) {
    const prev = roles
    setRoles({ ...roles, [key]: value })
    setSavingKey(key)
    setError('')
    try {
      const r = await fetch('/api/models/roles', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ [key]: value }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      // Keep the live chat header in sync with the main-chat assignment.
      if (key === 'active_model' && value) updateSettings({ activeModel: value })
      // The server keeps judge_fallback_model = last LLM judge picked; re-read it.
      if (key === 'judge_model') await loadRoles().catch(() => {})
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      setRoles(prev)
    } finally {
      setSavingKey(null)
    }
  }

  async function assignFallback(value: string) {
    const prev = judgeFallback
    setJudgeFallback(value)
    setSavingFallback(true)
    setError('')
    try {
      const r = await fetch('/api/models/roles', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ judge_fallback_model: value }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      setJudgeFallback(prev)
    } finally {
      setSavingFallback(false)
    }
  }

  const fallbackOptions = [
    { value: '', label: 'None — first installed small model' },
    ...(judgeFallback && !installedNames.includes(judgeFallback)
      ? [{ value: judgeFallback, label: `${judgeFallback} (not installed)` }] : []),
    ...installedNames.map(n => ({ value: n, label: n })),
  ]

  return (
    <div className="space-y-6 max-w-2xl">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-serif text-xl text-[var(--text-primary)] mb-1">Model Roles</h3>
          <p className="text-sm text-[var(--text-muted)]">
            Reassign the models chosen in setup, or pick from anything you've pulled into Ollama since.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => refresh(true)}
          disabled={refreshing}
          className="flex items-center gap-2 flex-shrink-0"
          title="Re-scan Ollama for installed models"
        >
          <RefreshCw className={clsx('w-3.5 h-3.5', refreshing && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      <Panel className="px-4 py-3 flex items-center gap-2 text-xs font-mono uppercase tracking-[0.18em] text-[var(--text-muted)]">
        <Boxes className="w-3.5 h-3.5" />
        {installedNames.length} model{installedNames.length === 1 ? '' : 's'} installed
      </Panel>

      {error && (
        <Panel className="px-4 py-3 border border-red-500/40 text-sm text-red-300">
          {error}
        </Panel>
      )}

      {loading ? (
        <div className="space-y-3">
          {ROLES.map(r => (
            <div key={r.key} className="h-[88px] rounded-xl glass-card animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
          {ROLES.map(role => (
            <Panel key={role.key} className="p-4 space-y-2.5">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-[var(--text-primary)]">{role.label}</div>
                  <p className="text-xs text-[var(--text-muted)] mt-0.5 leading-relaxed">{role.description}</p>
                </div>
                {savingKey === role.key && (
                  <Loader2 className="w-3.5 h-3.5 text-[var(--accent)] animate-spin flex-shrink-0" />
                )}
              </div>
              <Select
                value={roles[role.key]}
                onChange={v => assign(role.key, v)}
                options={optionsFor(role.key, roles[role.key])}
                placeholder={installedNames.length === 0 ? 'No models installed' : undefined}
              />
              {role.key === 'judge_model' && roles[role.key] === 'laya-builtin' && (
                <>
                  <Panel className="px-3 py-2.5 bg-blue-950/30 border border-blue-500/20 flex gap-2 items-start">
                    <AlertCircle className="w-3.5 h-3.5 text-blue-400 flex-shrink-0 mt-0.5" />
                    <p className="text-xs text-blue-300 leading-relaxed">
                      Fast (~0.2 s), no Ollama model needed, ~1.5 GB RAM while active. Early tests showed limited accuracy on chat routing, so it only decides when confident and otherwise falls back to the LLM judge below.
                    </p>
                  </Panel>
                  <div className="space-y-1.5" data-testid="judge-fallback">
                    <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)]">
                      Fallback LLM judge
                      {savingFallback && <Loader2 className="w-3 h-3 text-[var(--accent)] animate-spin" />}
                    </div>
                    <Select value={judgeFallback} onChange={assignFallback} options={fallbackOptions} />
                  </div>
                </>
              )}
              {role.key === 'judge_model' && (
                <LayaStatusCard compact onStatus={s => setLayaAvailable(s.available)} />
              )}
            </Panel>
          ))}
        </div>
      )}
    </div>
  )
}
