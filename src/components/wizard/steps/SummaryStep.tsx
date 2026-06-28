import { motion } from 'framer-motion'
import { User, Cpu, Eye, Code2, Database, Volume2, Palette, CheckCircle,
         ScanText, FileText, PenLine, Table, Wrench, Wand2 } from 'lucide-react'

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

export function SummaryStep({ config }: SummaryStepProps) {
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

      <p className="text-xs text-[var(--text-muted)] text-center">
        You can change any of these settings later from the Settings tab.
      </p>
    </div>
  )
}
