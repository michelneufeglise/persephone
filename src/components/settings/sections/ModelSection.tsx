import { useEffect, useState } from 'react'
import { useAppStore } from '@/store/appStore'
import { Slider } from '@/components/ui/Slider'
import { Input } from '@/components/ui/Input'
import { Select } from '@/components/ui/Select'
import { Panel } from '@/components/ui/Panel'

export function ModelSection() {
  const { settings, updateModelSettings, updateSettings } = useAppStore()
  const ms = settings.model
  const [detectedCores, setDetectedCores] = useState(0)

  useEffect(() => {
    fetch('/api/setup/hardware')
      .then(r => r.json())
      .then(d => setDetectedCores(d.cores ?? 0))
      .catch(() => {})
  }, [])

  return (
    <div className="space-y-6 max-w-2xl">
      <div>
        <h3 className="font-serif text-xl text-[var(--text-primary)] mb-1">Model Parameters</h3>
        <p className="text-sm text-[var(--text-muted)]">
          Fine-tune generation parameters. Applied to all Ollama models.
        </p>
      </div>

      <Panel className="p-4 space-y-5">
        <Slider
          label="Temperature"
          value={ms.temperature}
          min={0} max={2} step={0.05}
          onChange={v => updateModelSettings({ temperature: v })}
          format={v => v.toFixed(2)}
        />
        <Slider
          label="Top P (nucleus sampling)"
          value={ms.topP}
          min={0} max={1} step={0.01}
          onChange={v => updateModelSettings({ topP: v })}
          format={v => v.toFixed(2)}
        />
        <Slider
          label="Top K"
          value={ms.topK}
          min={1} max={100} step={1}
          onChange={v => updateModelSettings({ topK: v })}
          format={v => v.toFixed(0)}
        />
        <Slider
          label="Repeat Penalty"
          value={ms.repeatPenalty}
          min={1} max={2} step={0.01}
          onChange={v => updateModelSettings({ repeatPenalty: v })}
          format={v => v.toFixed(2)}
        />
      </Panel>

      <Panel className="p-4 space-y-5">
        <div className="grid grid-cols-2 gap-4">
          <Input
            label="Max Tokens"
            type="number"
            value={ms.maxTokens}
            onChange={e => updateModelSettings({ maxTokens: Number(e.target.value) })}
            min={1} max={32768}
          />
          <Input
            label="Context Length"
            type="number"
            value={ms.contextLength}
            onChange={e => updateModelSettings({ contextLength: Number(e.target.value) })}
            min={512} max={131072}
          />
          <Input
            label="Seed (-1 = random)"
            type="number"
            value={ms.seed}
            onChange={e => updateModelSettings({ seed: Number(e.target.value) })}
            min={-1}
          />
          <Input
            label="CPU Threads (0 = auto)"
            type="number"
            value={ms.numThread}
            onChange={e => updateModelSettings({ numThread: Math.max(0, Number(e.target.value)) })}
            min={0} max={64}
            hint={
              detectedCores > 0
                ? `0 = auto-detect — this machine has ${detectedCores} cores`
                : '0 = auto-detect based on your CPU'
            }
          />
        </div>
      </Panel>

      <Panel className="p-4 space-y-4">
        <Select
          label="Mirostat Mode"
          value={String(ms.mirostat)}
          onChange={v => updateModelSettings({ mirostat: Number(v) as 0 | 1 | 2 })}
          options={[
            { value: '0', label: '0 — Disabled' },
            { value: '1', label: '1 — Mirostat' },
            { value: '2', label: '2 — Mirostat 2.0' },
          ]}
        />
        {ms.mirostat > 0 && (
          <div className="grid grid-cols-2 gap-4">
            <Slider
              label="Mirostat τ (target entropy)"
              value={ms.mirostatTau}
              min={0.5} max={10} step={0.1}
              onChange={v => updateModelSettings({ mirostatTau: v })}
              format={v => v.toFixed(1)}
            />
            <Slider
              label="Mirostat η (learning rate)"
              value={ms.mirostatEta}
              min={0.01} max={1} step={0.01}
              onChange={v => updateModelSettings({ mirostatEta: v })}
              format={v => v.toFixed(2)}
            />
          </div>
        )}
      </Panel>

      <Panel className="p-4">
        <Input
          label="Ollama Host"
          value={settings.ollamaHost}
          onChange={e => updateSettings({ ollamaHost: e.target.value })}
          placeholder="http://localhost:11434"
          hint="Change if Ollama is running on a different host or port."
        />
      </Panel>
    </div>
  )
}
