import { useCallback, useEffect, useState } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { LlmConfig, NodeRunState } from '@/types/flows'
import * as flowActions from '@/lib/flowActions'
import { useFlowNodeContext } from '../FlowsView'
import { NodeShell } from '../NodeShell'

interface LlmNodeProps extends NodeProps {
  data: { config: LlmConfig }
}

export function LlmNode({ id, data, selected }: LlmNodeProps) {
  const context = useFlowNodeContext()
  const nodeState = context.getNodeState(id)
  const [models, setModels] = useState<string[]>([])

  useEffect(() => {
    flowActions.fetchModelsForFlow().then(setModels).catch(() => {})
  }, [])

  const handleChange = useCallback((updates: Partial<LlmConfig>) => {
    const newConfig = { ...data.config, ...updates }
    context.updateNodeConfig(id, newConfig)
  }, [id, data.config, context])

  return (
    <NodeShell
      nodeId={id}
      type="llm"
      runState={nodeState}
      isSelected={selected}
      handles={{ target: true, source: true }}
    >
      <div className="space-y-3">
        {/* Model selector */}
        <div className="space-y-1.5">
          <label className="text-xs text-[var(--text-muted)] block font-medium">Model</label>
          <select
            value={data.config.model}
            onChange={e => handleChange({ model: e.target.value })}
            className="w-full px-2 py-1.5 rounded-lg bg-[var(--bg-secondary)] text-xs text-[var(--text-primary)] border border-[var(--border)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)] transition-all"
          >
            <option value="">Select model…</option>
            {models.map(m => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>

        {/* System instruction */}
        <div className="space-y-1.5">
          <label className="text-xs text-[var(--text-muted)] block font-medium">System Instruction</label>
          <textarea
            value={data.config.prompt}
            onChange={e => handleChange({ prompt: e.target.value })}
            placeholder="Enter system instruction…"
            className="w-full px-2 py-2 rounded-lg bg-[var(--bg-secondary)] text-xs text-[var(--text-primary)] border border-[var(--border)] font-mono resize-none h-28 focus:outline-none focus:ring-1 focus:ring-[var(--accent)] transition-all"
          />
        </div>
      </div>
    </NodeShell>
  )
}
