import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, Loader2, Boxes } from 'lucide-react'
import { Panel } from '@/components/ui/Panel'
import { Select } from '@/components/ui/Select'
import { Button } from '@/components/ui/Button'
import { useAppStore } from '@/store/appStore'
import { fetchModels } from '@/lib/ollama'
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
    description: 'Reads handwritten notes, cursive, and signatures.',
  },
  {
    key: 'tables_model', label: 'Spreadsheets & Tables', required: false,
    description: 'Extracts tables and writes spreadsheet formulas.',
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
  ocr_model: '', docs_model: '', handwriting_model: '', tables_model: '',
  ableton_composer_model: '', ableton_deep_model: '',
}

export function ModelRolesSection() {
  const { models, setModels, updateSettings } = useAppStore()
  const [roles, setRoles]         = useState<RoleValues>(EMPTY_ROLES)
  const [loading, setLoading]     = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [savingKey, setSavingKey] = useState<RoleKey | null>(null)
  const [error, setError]         = useState('')

  const loadRoles = useCallback(async () => {
    const r = await fetch('/api/models/roles')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    setRoles({ ...EMPTY_ROLES, ...(await r.json()) })
  }, [])

  const refresh = useCallback(async (showSpinner: boolean) => {
    if (showSpinner) setRefreshing(true)
    setError('')
    try {
      const [list] = await Promise.all([fetchModels(), loadRoles()])
      setModels(list)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      if (showSpinner) setRefreshing(false)
      setLoading(false)
    }
  }, [loadRoles, setModels])

  useEffect(() => { refresh(false) }, [refresh])

  const installedNames = models
    .map(m => m.name)
    .filter(n => !n.toLowerCase().includes('embed'))
    .sort((a, b) => a.localeCompare(b))

  function optionsFor(roleKey: RoleKey, current: string) {
    const opts = installedNames.map(n => ({ value: n, label: n }))
    if (current && !installedNames.includes(current)) {
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
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
      setRoles(prev)
    } finally {
      setSavingKey(null)
    }
  }

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
            <div key={r.key} className="h-[88px] rounded-xl bg-[var(--bg-tertiary)] animate-pulse" />
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
            </Panel>
          ))}
        </div>
      )}
    </div>
  )
}
