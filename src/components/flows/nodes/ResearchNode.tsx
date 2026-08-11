import type { NodeProps } from '@xyflow/react'
import type { ResearchConfig, NodeRunState } from '@/types/flows'
import { useFlowNodeContext } from '../FlowsView'
import { NodeShell } from '../NodeShell'

interface ResearchNodeProps extends NodeProps {
  data: { config: ResearchConfig }
}

export function ResearchNode({ id, data, selected }: ResearchNodeProps) {
  const context = useFlowNodeContext()
  const nodeState = context.getNodeState(id)

  return (
    <NodeShell
      nodeId={id}
      type="research"
      runState={nodeState}
      isSelected={selected}
      handles={{ target: true, source: true }}
    >
      <div className="space-y-2">
        <p className="text-xs text-[var(--text-muted)] leading-relaxed">
          Runs deep research on the input query using multiple sources and cross-references.
        </p>
      </div>
    </NodeShell>
  )
}
