import { useCallback } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { DocOpConfig, NodeRunState } from '@/types/flows'
import { useFlowNodeContext } from '../FlowsView'
import { NodeShell } from '../NodeShell'

interface DocOpNodeProps extends NodeProps {
  data: { config: DocOpConfig }
}

const OPERATIONS = [
  'ocr', 'summarize', 'qa', 'tables', 'entities', 'classify', 'translate', 'redact', 'humanize',
] as const

export function DocOpNode({ id, data, selected }: DocOpNodeProps) {
  const context = useFlowNodeContext()
  const nodeState = context.getNodeState(id)

  const handleChange = useCallback((updates: Partial<DocOpConfig>) => {
    const newConfig = { ...data.config, ...updates }
    context.updateNodeConfig(id, newConfig)
  }, [id, data.config, context])

  return (
    <NodeShell
      nodeId={id}
      type="docop"
      runState={nodeState}
      isSelected={selected}
      handles={{ target: true, source: true }}
    >
      <div className="space-y-3">
        {/* Operation selector */}
        <div className="space-y-1.5">
          <label className="text-xs text-[var(--text-muted)] block font-medium">Operation</label>
          <select
            value={data.config.operation}
            onChange={e => handleChange({ operation: e.target.value as any })}
            className="w-full px-2 py-1.5 rounded-lg bg-[var(--bg-secondary)] text-xs text-[var(--text-primary)] border border-[var(--border)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)] transition-all"
          >
            {OPERATIONS.map(op => (
              <option key={op} value={op}>
                {op}
              </option>
            ))}
          </select>
        </div>

        {/* Context input */}
        <div className="space-y-1.5">
          <label className="text-xs text-[var(--text-muted)] block font-medium">Context</label>
          <input
            type="text"
            value={data.config.context || ''}
            onChange={e => handleChange({ context: e.target.value })}
            placeholder="e.g., question for qa, target language…"
            className="w-full px-2 py-1.5 rounded-lg bg-[var(--bg-secondary)] text-xs text-[var(--text-primary)] border border-[var(--border)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)] transition-all"
          />
        </div>
      </div>
    </NodeShell>
  )
}
