import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Cpu, MemoryStick, Zap, CheckCircle } from 'lucide-react'

interface HardwareInfo {
  os: string
  arch: string
  cpu: string
  ram_gb: number
  cores: number
  is_apple_silicon: boolean
  gpu: string
  tier: string
}

const TIER_LABELS: Record<string, { label: string; color: string; description: string }> = {
  ultra:   { label: 'Ultra',   color: 'text-[var(--accent)]',  description: 'Run any model including 70B+ with ease' },
  high:    { label: 'High',    color: 'text-purple-400',       description: 'Run large 30–40B models comfortably' },
  mid:     { label: 'Mid',     color: 'text-blue-400',         description: 'Ideal for 7–14B models at good speed' },
  low:     { label: 'Low',     color: 'text-amber-400',        description: 'Best with 3–7B quantized models' },
  minimal: { label: 'Minimal', color: 'text-red-400',          description: 'Use very small 1–3B models only' },
}

export function WelcomeStep() {
  const [hw, setHw] = useState<HardwareInfo | null>(null)

  useEffect(() => {
    fetch('/api/setup/hardware').then(r => r.json()).then(setHw).catch(() => {})
  }, [])

  const tier = hw ? (TIER_LABELS[hw.tier] ?? TIER_LABELS.low) : null

  return (
    <div className="flex flex-col items-center gap-8 max-w-xl mx-auto text-center">
      {/* Orb */}
      <motion.div
        className="relative w-40 h-40"
        animate={{ scale: [1, 1.04, 1] }}
        transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
      >
        <div className="absolute inset-0 rounded-full bg-gradient-to-br from-[var(--orb-color-1)] via-[var(--orb-color-2)] to-transparent
          shadow-2xl shadow-[var(--accent-glow)]" />
        <div className="absolute inset-4 rounded-full bg-gradient-to-tl from-transparent to-white/20" />
        <div className="absolute inset-0 flex items-center justify-center text-5xl select-none">⚘</div>
      </motion.div>

      <div>
        <h1 className="font-serif text-4xl text-[var(--text-primary)] mb-2">Welcome to Persephone</h1>
        <p className="text-[var(--text-secondary)] leading-relaxed max-w-sm">
          Your local AI companion. This wizard will configure your models, voice, and account in just a few steps.
        </p>
      </div>

      {/* Hardware card */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.3 }}
        className="w-full rounded-xl border border-[var(--border)] bg-[var(--bg-secondary)] p-5 text-left"
      >
        <div className="flex items-center gap-2 mb-4">
          <Zap className="w-4 h-4 text-[var(--accent)]" />
          <span className="text-sm font-medium text-[var(--text-primary)]">Hardware detected</span>
        </div>

        {!hw && (
          <div className="flex gap-2 items-center text-[var(--text-muted)] text-sm">
            <motion.div className="w-3 h-3 rounded-full bg-[var(--accent)]"
              animate={{ opacity: [0.3, 1, 0.3] }} transition={{ duration: 1, repeat: Infinity }} />
            Scanning your hardware…
          </div>
        )}

        {hw && (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <StatRow icon={Cpu} label="CPU" value={hw.cpu || 'Unknown'} />
              <StatRow icon={MemoryStick} label="RAM" value={`${hw.ram_gb} GB`} />
              {hw.gpu && <StatRow icon={Zap} label="GPU" value={hw.gpu} />}
              {hw.cores > 0 && <StatRow icon={Cpu} label="Cores" value={String(hw.cores)} />}
            </div>

            <div className="flex items-center gap-3 pt-2 border-t border-[var(--border)]">
              <div>
                <span className="text-xs text-[var(--text-muted)] uppercase tracking-wide">Performance tier</span>
                <div className={`text-lg font-semibold mt-0.5 font-serif ${tier?.color}`}>{tier?.label}</div>
              </div>
              <div className="flex-1">
                <p className="text-xs text-[var(--text-secondary)]">{tier?.description}</p>
              </div>
              {hw.is_apple_silicon && (
                <div className="flex items-center gap-1 px-2 py-1 rounded-full bg-[var(--accent-dim)] text-[10px] text-[var(--accent)] font-medium flex-shrink-0">
                  <CheckCircle className="w-3 h-3" />
                  Apple Silicon
                </div>
              )}
            </div>
          </div>
        )}
      </motion.div>

      <p className="text-xs text-[var(--text-muted)]">
        All data stays on your machine. No cloud, no telemetry.
      </p>
    </div>
  )
}

function StatRow({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: string }) {
  return (
    <div className="flex items-start gap-2">
      <Icon className="w-3.5 h-3.5 text-[var(--text-muted)] mt-0.5 flex-shrink-0" />
      <div className="min-w-0">
        <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wide">{label}</p>
        <p className="text-xs text-[var(--text-primary)] truncate" title={value}>{value}</p>
      </div>
    </div>
  )
}
