import { useCallback, useEffect, useState } from 'react'
import type { NodeProps } from '@xyflow/react'
import type { ToolConfig, NodeRunState } from '@/types/flows'
import * as flowActions from '@/lib/flowActions'
import { useFlowNodeContext } from '../FlowsView'
import { NodeShell } from '../NodeShell'

interface ToolNodeProps extends NodeProps {
  data: { config: ToolConfig }
}

export function ToolNode({ id, data, selected }: ToolNodeProps) {
  const context = useFlowNodeContext()
  const nodeState = context.getNodeState(id)
  const [tools, setTools] = useState<Array<{ name: string; description: string }>>([])

  useEffect(() => {
    flowActions.fetchTools().then(setTools).catch(() => {})
  }, [])

  const handleChange = useCallback((updates: Partial<ToolConfig>) => {
    const newConfig = { ...data.config, ...updates }
    context.updateNodeConfig(id, newConfig)
  }, [id, data.config, context])

  const selectedTool = tools.find(t => t.name === data.config.tool)

  return (
    <NodeShell
      nodeId={id}
      type="tool"
      runState={nodeState}
      isSelected={selected}
      handles={{ target: true, source: true }}
    >
      <div className="space-y-3">
        {/* Tool selector */}
        <div className="space-y-1.5">
          <label className="text-xs text-[var(--text-muted)] block font-medium">Tool</label>
          <select
            value={data.config.tool}
            onChange={e => handleChange({ tool: e.target.value })}
            className="w-full px-2 py-1.5 rounded-lg bg-[var(--bg-secondary)] text-xs text-[var(--text-primary)] border border-[var(--border)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)] transition-all"
          >
            <option value="">Select tool…</option>
            {tools.map(t => (
              <option key={t.name} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>
        </div>

        {/* Tool description */}
        {selectedTool && (
          <div className="text-xs text-[var(--text-muted)] bg-[var(--bg-secondary)] p-2 rounded-lg border border-[var(--border)]">
            {selectedTool.description}
          </div>
        )}
      </div>
    </NodeShell>
  )
}
