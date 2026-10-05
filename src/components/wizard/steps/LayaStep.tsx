import { FileSearch, Cpu, Languages, ListChecks } from 'lucide-react'
import { Toggle } from '@/components/ui/Toggle'
import { LayaStatusCard } from '@/components/laya/LayaStatusCard'
import type { LayaStatus } from '@/lib/idp'

interface LayaStepProps {
  /** Latest Laya status (owned by the wizard so the summary can show it). */
  status: LayaStatus | null
  onStatus: (s: LayaStatus) => void
  useAsRouter: boolean
  onUseAsRouterChange: (v: boolean) => void
  /** The LLM judge picked in the previous step — Laya's fallback. */
  fallbackJudge: string
}

const POINTS = [
  {
    Icon: ListChecks,
    text: 'A local classifier — it does not write text, it only chooses between options. About 421M parameters (a ModernBERT-large encoder).',
  },
  {
    Icon: FileSearch,
    text: 'In the Documents panel it decides the question type, the role of each file (letter, reference, signature card) and add-on steps such as a signature check or a web lookup.',
  },
  {
    Icon: Cpu,
    text: 'Runs on your machine (Apple GPU / MPS or CPU) and unloads itself after 10 minutes idle.',
  },
  {
    Icon: Languages,
    text: 'English only. If it is not installed or not sure, keyword rules take over, so everything still works.',
  },
]

export function LayaStep({ status, onStatus, useAsRouter, onUseAsRouterChange, fallbackJudge }: LayaStepProps) {
  const installed = !!status?.available

  return (
    <div className="max-w-2xl mx-auto space-y-5">
      <div>
        <h2 className="font-serif text-2xl text-[var(--text-primary)] mb-1">Decision model</h2>
        <p className="text-sm text-[var(--text-muted)]">
          Laya makes the small yes/no and multiple-choice decisions behind the Documents agent, quickly and
          without an Ollama model. Optional.
        </p>
      </div>

      <ul className="space-y-2">
        {POINTS.map(({ Icon, text }) => (
          <li key={text} className="flex items-start gap-2.5 text-[13px] text-[var(--text-secondary)] leading-relaxed">
            <Icon className="w-4 h-4 text-[var(--accent)] flex-shrink-0 mt-0.5" />
            <span>{text}</span>
          </li>
        ))}
      </ul>

      <LayaStatusCard onStatus={onStatus} />

      <div className="rounded-xl glass-card border border-[var(--glass-stroke)] p-4" data-testid="laya-router-toggle">
        <Toggle
          checked={useAsRouter && installed}
          disabled={!installed}
          onChange={onUseAsRouterChange}
          label="Also use Laya as the chat auto-router (experimental)"
          description={
            installed
              ? `Fast (~0.2 s) and needs no Ollama model, but less accurate on chat routing. It only decides when it is confident; otherwise your LLM judge${fallbackJudge ? ` (${fallbackJudge})` : ''} decides.`
              : 'Available once Laya is installed. Your LLM judge from the previous step stays in charge until then.'
          }
        />
      </div>

      <p className="text-xs text-[var(--text-muted)]">
        You can skip this step. Laya can be downloaded later from Settings → Models.
      </p>
    </div>
  )
}
