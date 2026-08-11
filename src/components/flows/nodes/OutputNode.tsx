import type { NodeProps } from '@xyflow/react'
import type { OutputConfig, NodeRunState } from '@/types/flows'
import { useFlowNodeContext } from '../FlowsView'
import { NodeShell } from '../NodeShell'

interface OutputNodeProps extends NodeProps {
  data: { config: OutputConfig }
}

export function OutputNode({ id, data, selected }: OutputNodeProps) {
  const context = useFlowNodeContext()
  const nodeState = context.getNodeState(id)

  return (
    <NodeShell
      nodeId={id}
      type="output"
      runState={nodeState}
      isSelected={selected}
      handles={{ target: true, source: false }}
    >
      <div className="space-y-2">
        <p className="text-xs text-[var(--text-muted)] leading-relaxed">
          Captures the final result from the flow for display and export.
        </p>
      </div>
    </NodeShell>
  )
}
