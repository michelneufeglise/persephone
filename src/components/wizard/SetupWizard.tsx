import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronRight, ChevronLeft, Sparkles } from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { WelcomeStep }  from './steps/WelcomeStep'
import { AccountStep }  from './steps/AccountStep'
import { ModelStep }    from './steps/ModelStep'
import { TTSStep }      from './steps/TTSStep'
import { MCPStep }      from './steps/MCPStep'
import { OllamaStep }   from './steps/OllamaStep'
import { SummaryStep }  from './steps/SummaryStep'
import { themes, applyTheme } from '@/themes'

const STEPS = [
  { id: 'welcome',     label: 'Welcome'      },
  { id: 'ollama',      label: 'Ollama'       },
  { id: 'account',     label: 'Account'      },
  { id: 'main-model',  label: 'Main Model'   },
  { id: 'judge',       label: 'Auto-router'  },
  { id: 'vision',      label: 'Vision'       },
  { id: 'code',        label: 'Code'         },
  { id: 'ocr',         label: 'OCR'          },
  { id: 'docs',        label: 'Documents'    },
  { id: 'handwriting', label: 'Handwriting'  },
  { id: 'tables',      label: 'Spreadsheets' },
  { id: 'tts',         label: 'Voice'        },
  { id: 'mcp',         label: 'Tools'        },
  { id: 'theme',       label: 'Theme'        },
  { id: 'summary',     label: 'Launch'       },
]

export function SetupWizard() {
  const { setWizardCompleted, setAccount, updateSettings, updateTTSSettings } = useAppStore()
  const [step, setStep] = useState(0)
  const [direction, setDirection] = useState(1)
  const [saving, setSaving] = useState(false)

  // Wizard state
  const [accountName, setAccountName]   = useState('Seeker')
  const [accountColor, setAccountColor] = useState('#8b2252')
  const [activeModel, setActiveModel]   = useState('')
  const [visionModel, setVisionModel]   = useState('')
  const [codeModel, setCodeModel]       = useState('')
  const [embedModel, setEmbedModel]     = useState('mxbai-embed-large')
  const [ocrModel, setOcrModel]                 = useState('')
  const [docsModel, setDocsModel]               = useState('')
  const [handwritingModel, setHandwritingModel] = useState('')
  const [tablesModel, setTablesModel]           = useState('')
  const [judgeModel, setJudgeModel]             = useState('qwen2.5:1.5b')
  const [ttsVoice, setTtsVoice]         = useState('af_heart')
  const [ttsSpeed, setTtsSpeed]         = useState(1.0)
  const [selectedTheme, setTheme]       = useState('underworld')
  const [mcpServers, setMcpServers]     = useState<string[]>(['fetch', 'duckduckgo-search', 'time', 'sequential-thinking'])
  const [ramGb, setRamGb]               = useState(0)

  useEffect(() => {
    fetch('/api/setup/hardware').then(r => r.json()).then(hw => setRamGb(hw.ram_gb ?? 0)).catch(() => {})
    // Pre-fill active model from Ollama
    fetch('/api/models').then(r => r.json()).then(d => {
      const models = (d.models ?? []) as Array<{name: string}>
      const chat = models.find(m =>
        !m.name.toLowerCase().includes('embed') &&
        !m.name.toLowerCase().includes('orpheus')
      )
      if (chat) setActiveModel(chat.name)
    }).catch(() => {})
  }, [])

  function goNext() {
    setDirection(1)
    setStep(s => Math.min(s + 1, STEPS.length - 1))
  }

  function goPrev() {
    setDirection(-1)
    setStep(s => Math.max(s - 1, 0))
  }

  async function handleLaunch() {
    setSaving(true)
    try {
      // Persist to backend
      await fetch('/api/setup/complete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_name:      accountName,
          account_color:     accountColor,
          active_model:      activeModel,
          vision_model:      visionModel,
          code_model:        codeModel,
          embed_model:       embedModel,
          ocr_model:         ocrModel,
          docs_model:        docsModel,
          handwriting_model: handwritingModel,
          tables_model:      tablesModel,
          judge_model:       judgeModel,
          tts_voice:         ttsVoice,
          tts_speed:         ttsSpeed,
          theme:             selectedTheme,
          mcp_servers:       mcpServers,
        }),
      })

      // Apply to Zustand store
      setAccount({ name: accountName, color: accountColor })
      updateSettings({ activeModel, theme: selectedTheme })
      updateTTSSettings({ voice: ttsVoice, speed: ttsSpeed })
      applyTheme(selectedTheme)
      setWizardCompleted(true)
    } catch {
      // Even if backend fails, complete wizard locally
      setAccount({ name: accountName, color: accountColor })
      updateSettings({ activeModel, theme: selectedTheme })
      updateTTSSettings({ voice: ttsVoice, speed: ttsSpeed })
      applyTheme(selectedTheme)
      setWizardCompleted(true)
    }
  }

  const isLast = step === STEPS.length - 1
  const isFirst = step === 0
  const progress = ((step) / (STEPS.length - 1)) * 100

  const variants = {
    enter: (dir: number) => ({ x: dir > 0 ? 40 : -40, opacity: 0 }),
    center: { x: 0, opacity: 1 },
    exit: (dir: number) => ({ x: dir > 0 ? -40 : 40, opacity: 0 }),
  }

  return (
    <div className="fixed inset-0 bg-[var(--bg-primary)] flex flex-col overflow-hidden">
      {/* Ambient background */}
      <div className="fixed inset-0 pointer-events-none"
        style={{
          background: `radial-gradient(ellipse at 20% 30%, var(--accent-glow) 0%, transparent 60%),
                       radial-gradient(ellipse at 80% 70%, var(--gold-dim) 0%, transparent 50%)`,
          opacity: 0.35,
        }}
      />

      {/* Header */}
      <div className="relative z-10 flex items-center justify-between px-8 py-5 border-b border-[var(--border)]">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[var(--orb-color-1)] to-[var(--orb-color-2)]
            flex items-center justify-center text-sm shadow-md shadow-[var(--accent-glow)]">
            ⚘
          </div>
          <span className="font-serif text-lg text-[var(--text-primary)]">Persephone Setup</span>
        </div>

        {/* Step dots */}
        <div className="flex items-center gap-1.5">
          {STEPS.map((s, i) => (
            <button
              key={s.id}
              onClick={() => i < step && (setDirection(i < step ? -1 : 1), setStep(i))}
              className="transition-all duration-300"
              title={s.label}
            >
              <div className={`rounded-full transition-all duration-300 ${
                i === step ? 'w-6 h-2 bg-[var(--accent)]' :
                i < step   ? 'w-2 h-2 bg-[var(--accent)] opacity-60' :
                             'w-2 h-2 bg-[var(--border-bright)]'
              }`} />
            </button>
          ))}
        </div>

        <div className="text-xs text-[var(--text-muted)] font-mono">
          {step + 1} / {STEPS.length}
        </div>
      </div>

      {/* Progress bar */}
      <div className="relative z-10 h-0.5 bg-[var(--bg-tertiary)]">
        <motion.div
          className="h-full bg-[var(--accent)]"
          animate={{ width: `${progress}%` }}
          transition={{ duration: 0.4, ease: 'easeInOut' }}
        />
      </div>

      {/* Step content */}
      <div className="relative z-10 flex-1 overflow-y-auto">
        <div className="min-h-full flex items-center justify-center px-6 py-10">
          <AnimatePresence custom={direction} mode="wait">
            <motion.div
              key={step}
              custom={direction}
              variants={variants}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.25, ease: 'easeInOut' }}
              className="w-full max-w-2xl"
            >
              {step === 0 && <WelcomeStep />}
              {step === 1 && <OllamaStep onReady={() => { /* keep user in control of advancing */ }} />}
              {step === 2 && (
                <AccountStep
                  name={accountName}
                  color={accountColor}
                  onNameChange={setAccountName}
                  onColorChange={setAccountColor}
                />
              )}
              {step === 3 && (
                <ModelStep
                  title="Main Chat Model"
                  subtitle="The primary model Persephone uses for conversation and reasoning."
                  category="chat"
                  selectedId={activeModel}
                  onSelect={setActiveModel}
                  ramGb={ramGb}
                />
              )}
              {step === 4 && (
                <ModelStep
                  title="Auto-router Judge"
                  subtitle="A tiny model that classifies each message so the router picks the right chat model. Runs in ~100ms, kept hot in memory. The smaller the better — accuracy vs. latency."
                  category="judge"
                  selectedId={judgeModel}
                  onSelect={setJudgeModel}
                  ramGb={ramGb}
                />
              )}
              {step === 5 && (
                <ModelStep
                  title="Vision Model"
                  subtitle="For analysing images, screenshots, and documents. Optional."
                  category="vision"
                  selectedId={visionModel}
                  onSelect={setVisionModel}
                  ramGb={ramGb}
                />
              )}
              {step === 6 && (
                <ModelStep
                  title="Code Model"
                  subtitle="Specialised for programming assistance. Optional — your main model can also code."
                  category="code"
                  selectedId={codeModel}
                  onSelect={setCodeModel}
                  ramGb={ramGb}
                />
              )}
              {step === 7 && (
                <ModelStep
                  title="OCR — Text Extraction"
                  subtitle="Extract text from scans, screenshots, photos of documents, and natural images. Optional."
                  category="ocr"
                  selectedId={ocrModel}
                  onSelect={setOcrModel}
                  ramGb={ramGb}
                />
              )}
              {step === 8 && (
                <ModelStep
                  title="Documents & PDF"
                  subtitle="Read, query, and reason about PDFs, contracts, invoices, and multi-page documents. Optional."
                  category="docs"
                  selectedId={docsModel}
                  onSelect={setDocsModel}
                  ramGb={ramGb}
                />
              )}
              {step === 9 && (
                <ModelStep
                  title="Handwriting"
                  subtitle="Read handwritten notes, cursive, signatures, and historical scripts. Optional."
                  category="handwriting"
                  selectedId={handwritingModel}
                  onSelect={setHandwritingModel}
                  ramGb={ramGb}
                />
              )}
              {step === 10 && (
                <ModelStep
                  title="Spreadsheets & Tables"
                  subtitle="Extract tables from PDFs, write Excel formulas, automate spreadsheet workflows. Optional."
                  category="tables"
                  selectedId={tablesModel}
                  onSelect={setTablesModel}
                  ramGb={ramGb}
                />
              )}
              {step === 11 && (
                <TTSStep
                  voice={ttsVoice}
                  speed={ttsSpeed}
                  onVoiceChange={setTtsVoice}
                  onSpeedChange={setTtsSpeed}
                />
              )}
              {step === 12 && (
                <MCPStep selected={mcpServers} onChange={setMcpServers} />
              )}
              {step === 13 && (
                <ThemeStep selected={selectedTheme} onSelect={t => { setTheme(t); applyTheme(t) }} />
              )}
              {step === 14 && (
                <SummaryStep config={{
                  accountName, accountColor,
                  activeModel, visionModel, codeModel, embedModel,
                  ocrModel, docsModel, handwritingModel, tablesModel,
                  judgeModel,
                  ttsVoice, ttsSpeed, theme: selectedTheme,
                  mcpCount: mcpServers.length,
                }} />
              )}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>

      {/* Footer nav */}
      <div className="relative z-10 flex items-center justify-between px-8 py-5 border-t border-[var(--border)]">
        <button
          onClick={goPrev}
          disabled={isFirst}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
            text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-tertiary)]
            transition-all disabled:opacity-0 disabled:pointer-events-none"
        >
          <ChevronLeft className="w-4 h-4" />
          Back
        </button>

        {isLast ? (
          <motion.button
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            onClick={handleLaunch}
            disabled={saving}
            className="flex items-center gap-2 px-8 py-3 rounded-xl font-semibold text-sm
              bg-[var(--accent)] text-white shadow-lg shadow-[var(--accent-glow)]
              hover:bg-[var(--accent-hover)] transition-all disabled:opacity-60"
          >
            {saving ? (
              <motion.div className="w-4 h-4 rounded-full border-2 border-white/30 border-t-white"
                animate={{ rotate: 360 }} transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }} />
            ) : (
              <Sparkles className="w-4 h-4" />
            )}
            {saving ? 'Launching…' : 'Launch Persephone'}
          </motion.button>
        ) : (
          <button
            onClick={goNext}
            className="flex items-center gap-2 px-6 py-2.5 rounded-xl text-sm font-semibold
              bg-[var(--accent)] text-white shadow-md shadow-[var(--accent-glow)]
              hover:bg-[var(--accent-hover)] transition-all active:scale-95"
          >
            {step === 0 ? 'Begin' : 'Next'}
            <ChevronRight className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  )
}

// ── Inline theme step ──────────────────────────────────────────────────────────
function ThemeStep({ selected, onSelect }: { selected: string; onSelect: (id: string) => void }) {
  return (
    <div className="max-w-lg mx-auto space-y-5">
      <div>
        <h2 className="font-serif text-2xl text-[var(--text-primary)] mb-1">Choose Your Theme</h2>
        <p className="text-sm text-[var(--text-muted)]">The look and feel of your Persephone. Can be changed anytime.</p>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {themes.map(theme => (
          <button
            key={theme.id}
            onClick={() => onSelect(theme.id)}
            className={`relative flex items-center gap-4 p-4 rounded-xl border text-left transition-all duration-300 ${
              selected === theme.id
                ? 'scale-[1.02] shadow-lg'
                : 'hover:scale-[1.005] opacity-90 hover:opacity-100'
            }`}
            style={{
              background: theme.preview.bg,
              border: `2px solid ${selected === theme.id ? theme.preview.accent : 'rgba(255,255,255,0.08)'}`,
              boxShadow: selected === theme.id ? `0 0 20px ${theme.preview.accent}40` : undefined,
            }}
          >
            <div className="flex gap-1.5 flex-shrink-0">
              {[theme.preview.bg, theme.preview.accent, theme.preview.text].map((c, i) => (
                <div key={i} className="w-6 h-6 rounded-full border border-white/10" style={{ background: c }} />
              ))}
            </div>
            <div>
              <div className="font-serif text-sm font-medium" style={{ color: theme.preview.text }}>{theme.name}</div>
              <div className="text-[11px] mt-0.5 opacity-60" style={{ color: theme.preview.text }}>{theme.description}</div>
            </div>
            {selected === theme.id && (
              <div className="absolute top-2 right-2 w-4 h-4 rounded-full flex items-center justify-center"
                style={{ background: theme.preview.accent }}>
                <svg viewBox="0 0 12 12" className="w-2.5 h-2.5 text-white fill-current">
                  <path d="M2 6l3 3 5-5" stroke="white" strokeWidth="1.5" fill="none" strokeLinecap="round" />
                </svg>
              </div>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}
