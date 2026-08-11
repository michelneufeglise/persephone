import type { Flow, NodeRunState } from '@/types/flows'
import * as flowActions from '@/lib/flowActions'

export type NodeStateCallback = (nodeId: string, state: NodeRunState) => void

/**
 * Topologically sort nodes by edges. Returns nodes in execution order.
 * Throws if a cycle is detected.
 */
function toposort(flow: Flow): Flow['nodes'] {
  const nodes = new Map(flow.nodes.map(n => [n.id, n]))
  const inDegree = new Map<string, number>()
  const adjacency = new Map<string, string[]>()

  // Build adjacency list and in-degree map
  for (const node of flow.nodes) {
    inDegree.set(node.id, 0)
    adjacency.set(node.id, [])
  }

  for (const edge of flow.edges) {
    if (nodes.has(edge.source) && nodes.has(edge.target)) {
      const current = inDegree.get(edge.target) || 0
      inDegree.set(edge.target, current + 1)
      const adj = adjacency.get(edge.source) || []
      adj.push(edge.target)
      adjacency.set(edge.source, adj)
    }
  }

  // Kahn's algorithm
  const queue = [...inDegree.entries()]
    .filter(([_, degree]) => degree === 0)
    .map(([id]) => id)

  const sorted: Flow['nodes'] = []
  while (queue.length > 0) {
    const nodeId = queue.shift()!
    const node = nodes.get(nodeId)
    if (node) sorted.push(node)

    for (const neighbor of adjacency.get(nodeId) || []) {
      const newDegree = (inDegree.get(neighbor) || 0) - 1
      inDegree.set(neighbor, newDegree)
      if (newDegree === 0) queue.push(neighbor)
    }
  }

  if (sorted.length !== flow.nodes.length) {
    throw new Error('Cycle detected in flow graph')
  }

  return sorted
}

/**
 * Find incoming edges for a node.
 */
function getUpstreamNodeIds(flow: Flow, nodeId: string): string[] {
  return flow.edges
    .filter(e => e.target === nodeId)
    .map(e => e.source)
}

/**
 * Execute a flow in topological order. Each node receives the output
 * of its upstream predecessor(s), or for multiple inputs, their outputs
 * are concatenated as text. For input nodes, their config is used.
 *
 * Calls onNodeState with live updates; throws on the first node error.
 */
export async function runFlow(
  flow: Flow,
  onNodeState: NodeStateCallback,
): Promise<unknown> {
  const sorted = toposort(flow)
  const outputs = new Map<string, unknown>()

  for (const node of sorted) {
    onNodeState(node.id, { status: 'running' })

    try {
      let nodeOutput: unknown

      if (node.type === 'input') {
        const cfg = node.data.config as any
        nodeOutput = cfg.kind === 'document' ? cfg.documentId : cfg.text || ''
      } else if (node.type === 'output') {
        // Output node just passes through the upstream
        const upstreams = getUpstreamNodeIds(flow, node.id)
        nodeOutput = upstreams.length > 0 ? outputs.get(upstreams[0]) : null
      } else {
        // For other nodes, collect upstream outputs
        const upstreams = getUpstreamNodeIds(flow, node.id)
        let input: unknown
        if (upstreams.length === 0) {
          input = null
        } else if (upstreams.length === 1) {
          input = outputs.get(upstreams[0])
        } else {
          // Multiple inputs: concatenate as strings
          input = upstreams
            .map(uid => String(outputs.get(uid) || ''))
            .join('\n')
        }

        switch (node.type) {
          case 'llm':
            nodeOutput = await flowActions.runLlm(node.data.config as any, input)
            break
          case 'research':
            nodeOutput = await flowActions.runResearch(node.data.config as any, input)
            break
          case 'docop':
            nodeOutput = await flowActions.runDocOp(node.data.config as any, input)
            break
          case 'tool':
            nodeOutput = await flowActions.runTool(node.data.config as any, input)
            break
          case 'code':
            nodeOutput = await flowActions.runCode(node.data.config as any, input)
            break
          default:
            throw new Error(`Unknown node type: ${node.type}`)
        }
      }

      outputs.set(node.id, nodeOutput)
      onNodeState(node.id, { status: 'done', output: nodeOutput })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      onNodeState(node.id, { status: 'error', error: message })
      throw error
    }
  }

  // Return the output node's value if it exists, otherwise the last node's output
  const outputNodes = sorted.filter(n => n.type === 'output')
  if (outputNodes.length > 0) {
    return outputs.get(outputNodes[0].id)
  }

  return outputs.get(sorted[sorted.length - 1].id)
}
