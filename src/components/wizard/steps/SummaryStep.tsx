import { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { User, Cpu, Eye, Code2, Database, Volume2, Palette, CheckCircle,
         ScanText, FileText, PenLine, Table, Wrench, Wand2, Loader2,
         AlertTriangle, Download } from 'lucide-react'

interface Config {
  accountName: string
  accountColor: string
  activeModel: string
  visionModel: string
  codeModel: string
  embedModel: string
  ocrModel: string
  docsModel: string
  handwritingModel: string
  tablesModel: string
  judgeModel?: string
  ttsVoice: string
  ttsSpeed: number
  theme: string
  mcpCount?: number
}

interface SummaryStepProps {
  config: Config
}

interface TtsStatus {
  ready:              boolean
  package_installed:  boolean
  model_downloaded:   boolean
  voices_downloaded:  boolean
  model_size_mb:      number
  voices_size_mb:     number
  missing:            string[]
}

export function SummaryStep({ config }: SummaryStepProps) {
  const [ttsStatus, setTtsStatus]     = useState<TtsStatus | null>(null)
  const [ttsInstalling, setTtsInstalling] = useState(false)
  const [ttsError, setTtsError]       = useState<string>('')

  const loadStatus = useCallback(async () => {
    try {
      const r = await fetch('/api/setup/tts-status')
      if (r.ok) setTtsStatus(await r.json())
    } catch { /* silent */ }
  }, [])

  // Auto-install on first mount if not ready — the wizard is the natural
  // place to sink a one-time 100-500MB download so the first spoken reply
  // doesn't surprise the user with wait time later.
  useEffect(() => {
    (async () => {
      await loadStatus()
      try {
        const r = await fetch('/api/setup/tts-status')
        const s = await r.json() as TtsStatus
        if (!s.ready && s.package_installed) {
          setTtsInstalling(true)
          setTtsError('')
          try {
            const ir = await fetch('/api/setup/tts-install', { method: 'POST' })
            const id = await ir.json() as TtsStatus & { ok?: boolean; error?: string }
            setTtsStatus(id)
            if (!id.ok && id.error) setTtsError(id.error)
          } catch (exc) {
            setTtsError(exc instanceof Error ? exc.message : String(exc))
          } finally {
            setTtsInstalling(false)
          }
        } else {
          setTtsStatus(s)
        }
      } catch { /* silent */ }
    })()
  }, [loadStatus])

  const rows = [
    { icon: User,      label: 'Account',          value: config.accountName        || 'Not set'     },
    { icon: Cpu,       label: 'Main model',       value: config.activeModel        || 'Not selected'},
    { icon: Wand2,     label: 'Auto-router',      value: config.judgeModel         || 'Skipped'     },
    { icon: Eye,       label: 'Vision model',     value: config.visionModel        || 'Skipped'     },
    { icon: Code2,     label: 'Code model',       value: config.codeModel          || 'Skipped'     },
    { icon: Database,  label: 'Embed model',      value: config.embedModel         || 'Skipped'     },
    { icon: ScanText,  label: 'OCR model',        value: config.ocrModel           || 'Skipped'     },
    { icon: FileText,  label: 'Docs / PDF',       value: config.docsModel          || 'Skipped'     },
    { icon: PenLine,   label: 'Handwriting',      value: config.handwritingModel   || 'Skipped'     },
    { icon: Table,     label: 'Spreadsheets',     value: config.tablesModel        || 'Skipped'     },
    { icon: Volume2,   label: 'TTS voice',        value: `${config.ttsVoice} (${config.ttsSpeed.toFixed(2)}×)` },
    { icon: Wrench,    label: 'MCP tools',        value: config.mcpCount ? `${config.mcpCount} enabled` : 'None' },
    { icon: Palette,   label: 'Theme',            value: config.theme              },
  ]

  return (
    <div className="max-w-md mx-auto space-y-8">
      {/* Big checkmark */}
      <div className="flex flex-col items-center gap-4">
        <motion.div
          initial={{ scale: 0, rotate: -180 }}
          animate={{ scale: 1, rotate: 0 }}
          transition={{ type: 'spring', stiffness: 200, damping: 15 }}
          className="w-20 h-20 rounded-full bg-gradient-to-br from-[var(--orb-color-1)] to-[var(--orb-color-2)]
            flex items-center justify-center shadow-xl shadow-[var(--accent-glow)]"
        >
          <CheckCircle className="w-10 h-10 text-white" />
        </motion.div>
        <div className="text-center">
          <h2 className="font-serif text-2xl text-[var(--text-primary)]">All set!</h2>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Review your configuration below, then launch Persephone.
          </p>
        </div>
      </div>

      {/* Config summary */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] overflow-hidden">
        {rows.map(({ icon: Icon, label, value }, i) => (
          <motion.div
            key={label}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.05 * i }}
            className={`flex items-center gap-3 px-4 py-3 ${
              i < rows.length - 1 ? 'border-b border-[var(--border)]' : ''
            }`}
          >
            <div className="w-7 h-7 rounded-lg bg-[var(--accent-dim)] flex items-center justify-center flex-shrink-0">
              <Icon className="w-3.5 h-3.5 text-[var(--accent)]" />
            </div>
            <span className="text-xs text-[var(--text-muted)] w-28 flex-shrink-0">{label}</span>
            <span className="text-sm text-[var(--text-primary)] truncate font-mono">
              {value}
            </span>
          </motion.div>
        ))}
      </div>

      {/* TTS install status — shown so the user sees Kokoro finishing on-screen */}
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)]/60 px-4 py-3 space-y-1.5">
        <div className="flex items-center gap-2 text-xs">
          <Volume2 className="w-3.5 h-3.5 text-[var(--accent)]" />
          <span className="text-[var(--text-primary)] font-medium">Voice engine</span>
          {ttsInstalling && <>
            <Loader2 className="w-3 h-3 animate-spin text-[var(--accent)]" />
            <span className="text-[var(--text-muted)]">downloading Kokoro model (~360 MB)…</span>
          </>}
          {!ttsInstalling && ttsStatus?.ready && <>
            <CheckCircle className="w-3 h-3 text-emerald-400" />
            <span className="text-[var(--text-muted)]">
              ready ({ttsStatus.model_size_mb} MB model + {ttsStatus.voices_size_mb} MB voices, warmed up)
            </span>
          </>}
          {!ttsInstalling && ttsStatus && !ttsStatus.ready && (
            <>
              <AlertTriangle className="w-3 h-3 text-amber-400" />
              <span className="text-[var(--text-muted)]">
                {ttsStatus.missing.length > 0 ? `Missing: ${ttsStatus.missing.join(', ')}` : 'Not ready yet'}
              </span>
              <button
                onClick={async () => {
                  setTtsInstalling(true)
                  try {
                    const r = await fetch('/api/setup/tts-install', { method: 'POST' })
                    const d = await r.json() as TtsStatus & { ok?: boolean; error?: string }
                    setTtsStatus(d)
                    if (!d.ok && d.error) setTtsError(d.error); else setTtsError('')
                  } catch (exc) {
                    setTtsError(exc instanceof Error ? exc.message : String(exc))
                  } finally { setTtsInstalling(false) }
                }}
                className="ml-auto flex items-center gap-1 text-[10px] px-2 py-0.5 rounded border border-[var(--accent)] text-[var(--accent)] hover:bg-[var(--accent-dim)]"
              >
                <Download className="w-2.5 h-2.5" /> retry
              </button>
            </>
          )}
        </div>
        {ttsError && (
          <div className="text-[10.5px] text-red-300 italic">{ttsError}</div>
        )}
      </div>

      <p className="text-xs text-[var(--text-muted)] text-center">
        You can change any of these settings later from the Settings tab.
      </p>
    </div>
  )
}
