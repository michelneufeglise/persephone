import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronRight, ChevronLeft, Sparkles } from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { PersephoneIcon } from '@/components/PersephoneIcon'
import { WelcomeStep }  from './steps/WelcomeStep'
import { AccountStep }  from './steps/AccountStep'
import { ModelStep }    from './steps/ModelStep'
import { TTSStep }      from './steps/TTSStep'
import { MCPStep }      from './steps/MCPStep'
import { OllamaStep }   from './steps/OllamaStep'
import { SummaryStep }  from './steps/SummaryStep'
import { LayaStep }     from './steps/LayaStep'
import type { LayaStatus } from '@/lib/idp'
import { themes, applyTheme } from '@/themes'

const STEPS = [
  { id: 'welcome',     label: 'Welcome'      },
  { id: 'ollama',      label: 'Ollama'       },
  { id: 'account',     label: 'Account'      },
  { id: 'main-model',  label: 'Main Model'   },
  { id: 'judge',       label: 'Auto-router'  },
  { id: 'laya',        label: 'Decision model' },
  { id: 'vision',      label: 'Vision'       },
  { id: 'code',        label: 'Code'         },
  { id: 'ocr',         label: 'OCR'          },
  { id: 'docs',        label: 'Documents'    },
  { id: 'handwriting', label: 'Handwriting'  },
  { id: 'signature',   label: 'Signature verification' },
  { id: 'tables',      label: 'Spreadsheets' },
  { id: 'embed',       label: 'Embeddings'   },
  { id: 'multidoc',    label: 'Multi-Doc'    },
  { id: 'tts',         label: 'Voice'        },
  { id: 'mcp',         label: 'Tools'        },
  { id: 'theme',       label: 'Theme'        },
  { id: 'summary',     label: 'Launch'       },
] as const

type StepId = (typeof STEPS)[number]['id']

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
  const [signatureModel, setSignatureModel]     = useState('')
  const [tablesModel, setTablesModel]           = useState('')
  const [multidocModel, setMultidocModel]       = useState('')
  const [judgeModel, setJudgeModel]             = useState('qwen2.5:1.5b')
  const [layaStatus, setLayaStatus]             = useState<LayaStatus | null>(null)
  const [layaAsRouter, setLayaAsRouter]         = useState(false)
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

  // Laya only replaces the chat judge when it is installed AND the user
  // opted in; the LLM judge from the Auto-router step stays its fallback.
  const useLayaRouter = layaAsRouter && !!layaStatus?.available

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
          signature_model:   signatureModel,
          tables_model:      tablesModel,
          multidoc_model:    multidocModel,
          judge_model:       useLayaRouter ? 'laya-builtin' : judgeModel,
          judge_fallback_model: judgeModel,
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

  const current: StepId = STEPS[step].id
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
      <div className="atmos atmos-wallpaper" />
      <div className="atmos atmos-backdrop" />

      {/* Ambient background */}
      <div className="fixed inset-0 pointer-events-none"
        style={{
          background: `radial-gradient(ellipse at 20% 30%, var(--accent-glow) 0%, transparent 60%),
                       radial-gradient(ellipse at 80% 70%, var(--gold-dim) 0%, transparent 50%)`,
          opacity: 0.35,
        }}
      />

      {/* Header */}
      <div className="relative z-10 flex items-center justify-between px-8 py-5 border-b border-[var(--glass-stroke)]">
        <div className="flex items-center gap-2">
          <PersephoneIcon size={28} glow={false} />
          <span className="font-serif text-lg text-[var(--text-primary)]">Persephone Setup</span>
        </div>

        {/* Step dots */}
        <div className="relative z-10 flex items-center gap-1.5">
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

        <div className="relative z-10 text-xs text-[var(--text-muted)] font-mono">
          {step + 1} / {STEPS.length}
        </div>
      </div>

      {/* Progress bar */}
      <div className="relative z-10 h-0.5 bg-[var(--glass-fill-hover)]">
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
              {current === 'welcome' && <WelcomeStep />}
              {current === 'ollama' && <OllamaStep onReady={() => { /* keep user in control of advancing */ }} />}
              {current === 'account' && (
                <AccountStep
                  name={accountName}
                  color={accountColor}
                  onNameChange={setAccountName}
                  onColorChange={setAccountColor}
                />
              )}
              {current === 'main-model' && (
                <ModelStep
                  title="Main Chat Model"
                  subtitle="The primary model Persephone uses for conversation and reasoning."
                  category="chat"
                  selectedId={activeModel}
                  onSelect={setActiveModel}
                  ramGb={ramGb}
                />
              )}
              {current === 'judge' && (
                <ModelStep
                  title="Auto-router Judge"
                  subtitle="A tiny model that classifies each message so the router picks the right chat model. Runs in ~100ms, kept hot in memory. The smaller the better — accuracy vs. latency."
                  category="judge"
                  selectedId={judgeModel}
                  onSelect={setJudgeModel}
                  ramGb={ramGb}
                />
              )}
              {current === 'laya' && (
                <LayaStep
                  status={layaStatus}
                  onStatus={setLayaStatus}
                  useAsRouter={layaAsRouter}
                  onUseAsRouterChange={setLayaAsRouter}
                  fallbackJudge={judgeModel}
                />
              )}
              {current === 'vision' && (
                <ModelStep
                  title="Vision Model"
                  subtitle="For analysing images, screenshots, and documents. Optional."
                  category="vision"
                  selectedId={visionModel}
                  onSelect={setVisionModel}
                  ramGb={ramGb}
                />
              )}
              {current === 'code' && (
                <ModelStep
                  title="Code Model"
                  subtitle="Specialised for programming assistance. Optional — your main model can also code."
                  category="code"
                  selectedId={codeModel}
                  onSelect={setCodeModel}
                  ramGb={ramGb}
                />
              )}
              {current === 'ocr' && (
                <ModelStep
                  title="OCR — Text Extraction"
                  subtitle="Extract text from scans, screenshots, photos of documents, and natural images. Optional."
                  category="ocr"
                  selectedId={ocrModel}
                  onSelect={setOcrModel}
                  ramGb={ramGb}
                />
              )}
              {current === 'docs' && (
                <ModelStep
                  title="Documents & PDF"
                  subtitle="Read, query, and reason about PDFs, contracts, invoices, and multi-page documents. Optional."
                  category="docs"
                  selectedId={docsModel}
                  onSelect={setDocsModel}
                  ramGb={ramGb}
                />
              )}
              {current === 'handwriting' && (
                <ModelStep
                  title="Handwriting"
                  subtitle="Read handwritten notes, cursive, signatures, and historical scripts. Optional."
                  category="handwriting"
                  selectedId={handwritingModel}
                  onSelect={setHandwritingModel}
                  ramGb={ramGb}
                />
              )}
              {current === 'signature' && (
                <ModelStep
                  title="Signature verification"
                  subtitle="Compares a signature with reference signatures in the Documents panel. A local engine computes the similarity score; this vision model finds the signature on the page and explains the similarities and differences. Pick a vision-capable model, not an OCR-only one. If you skip this, the Handwriting model is used, then the Vision model."
                  category="signature"
                  selectedId={signatureModel}
                  onSelect={setSignatureModel}
                  ramGb={ramGb}
                />
              )}
              {current === 'tables' && (
                <ModelStep
                  title="Spreadsheets & Tables"
                  subtitle="Extract tables from PDFs, write Excel formulas, automate spreadsheet workflows. Optional."
                  category="tables"
                  selectedId={tablesModel}
                  onSelect={setTablesModel}
                  ramGb={ramGb}
                />
              )}
              {current === 'embed' && (
                <ModelStep
                  title="Embedding Model"
                  subtitle="Powers semantic search, document RAG, and long-term memory. mxbai-embed-large is a solid default."
                  category="embed"
                  selectedId={embedModel}
                  onSelect={setEmbedModel}
                  ramGb={ramGb}
                />
              )}
              {current === 'multidoc' && (
                <ModelStep
                  title="Multi-Document Query"
                  subtitle="Pick the model that answers questions across several selected documents at once. A capable long-context chat model works best."
                  category="chat"
                  selectedId={multidocModel}
                  onSelect={setMultidocModel}
                  ramGb={ramGb}
                />
              )}
              {current === 'tts' && (
                <TTSStep
                  voice={ttsVoice}
                  speed={ttsSpeed}
                  onVoiceChange={setTtsVoice}
                  onSpeedChange={setTtsSpeed}
                />
              )}
              {current === 'mcp' && (
                <MCPStep selected={mcpServers} onChange={setMcpServers} />
              )}
              {current === 'theme' && (
                <ThemeStep selected={selectedTheme} onSelect={t => { setTheme(t); applyTheme(t) }} />
              )}
              {current === 'summary' && (
                <SummaryStep config={{
                  accountName, accountColor,
                  activeModel, visionModel, codeModel, embedModel,
                  ocrModel, docsModel, handwritingModel, signatureModel, tablesModel,
                  judgeModel,
                  laya: {
                    installed: !!layaStatus?.available,
                    known: layaStatus !== null,
                    usedAsRouter: useLayaRouter,
                  },
                  ttsVoice, ttsSpeed, theme: selectedTheme,
                  mcpCount: mcpServers.length,
                }} />
              )}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>

      {/* Footer nav */}
      <div className="relative z-10 flex items-center justify-between px-8 py-5 border-t border-[var(--glass-stroke)]">
        <button
          onClick={goPrev}
          disabled={isFirst}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium
            text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--glass-fill-hover)]
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
              background: theme.preview.wall
                ? `radial-gradient(60% 70% at 15% 20%, ${theme.preview.wall[0]} 0%, transparent 70%), radial-gradient(60% 70% at 85% 15%, ${theme.preview.wall[1]} 0%, transparent 70%), radial-gradient(60% 70% at 80% 90%, ${theme.preview.wall[2]} 0%, transparent 70%), radial-gradient(50% 60% at 15% 90%, ${theme.preview.wall[3]} 0%, transparent 72%), ${theme.preview.bg}`
                : theme.preview.bg,
              border: `2px solid ${selected === theme.id ? theme.preview.accent : 'rgba(255,255,255,0.08)'}`,
              boxShadow: selected === theme.id ? `0 0 20px ${theme.preview.accent}40` : undefined,
            }}
          >
            <div className="flex gap-1.5 flex-shrink-0">
              {[theme.preview.bg, theme.preview.accent, theme.preview.text].map((c, i) => (
                <div key={i} className="w-6 h-6 rounded-full border border-white/10" style={{ background: c }} />
              ))}
            </div>
            <div className="glass rounded-xl p-3">
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
