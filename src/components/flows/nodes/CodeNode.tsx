import { useCallback } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { CodeConfig, NodeRunState } from '@/types/flows'
import { useFlowNodeContext } from '../FlowsView'
import { NodeShell } from '../NodeShell'

interface CodeNodeProps extends NodeProps {
  data: { config: CodeConfig }
}

export function CodeNode({ id, data, selected }: CodeNodeProps) {
  const context = useFlowNodeContext()
  const nodeState = context.getNodeState(id)

  const handleChange = useCallback((updates: Partial<CodeConfig>) => {
    const newConfig = { ...data.config, ...updates }
    context.updateNodeConfig(id, newConfig)
  }, [id, data.config, context])

  return (
    <NodeShell
      nodeId={id}
      type="code"
      runState={nodeState}
      isSelected={selected}
      handles={{ target: true, source: true }}
    >
      <div className="space-y-3">
        {/* Code editor */}
        <div className="space-y-1.5">
          <label className="text-xs text-[var(--text-muted)] block font-medium">TypeScript Code</label>
          <textarea
            value={data.config.code}
            onChange={e => handleChange({ code: e.target.value })}
            placeholder="// 'input' is in scope&#10;// must return a value&#10;return input.toUpperCase();"
            className="w-full px-2 py-2 rounded-lg bg-[var(--bg-secondary)] text-xs text-[var(--text-primary)] border border-[var(--border)] font-mono resize-none h-32 focus:outline-none focus:ring-1 focus:ring-[var(--accent)] transition-all leading-relaxed"
          />
        </div>

        {/* Help text */}
        <p className="text-xs text-[var(--text-muted)] opacity-75">
          Variable <code className="text-[var(--accent-dim)]">input</code> is in scope. Must return a value.
        </p>
      </div>
    </NodeShell>
  )
}
