import * as d3Force from 'd3-force'
import { NODE_SIZES } from './kgModel'
import type { KGraph, LayoutNode, LayoutEdge } from './kgModel'

export interface LayoutForceOptions {
  width: number
  height: number
  previous?: Map<string, { x: number; y: number }>
  pinned?: Map<string, { x: number; y: number }>
  previousVirtualSize?: { width: number; height: number }
}

export interface LayoutForceNode {
  id: string
  kind: 'document' | 'question' | 'decision' | 'model' | 'answer'
  x: number
  y: number
  vx?: number
  vy?: number
  fx?: number | null
  fy?: number | null
}

export interface LayoutForceResult {
  nodes: LayoutNode[]
  edges: LayoutEdge[]
  virtualWidth: number
  virtualHeight: number
}

/**
 * Force-directed layout using d3-force with virtual layout box scaling.
 * Computes needed space and scales the layout box if the container is too small.
 * Uses optimized collision detection and rectangle overlap resolution.
 *
 * @param graph The knowledge graph
 * @param options Width, height of the container, and optional previous positions
 * @returns Positioned layout nodes, edges, and virtual dimensions
 */
export function layoutForce(
  graph: KGraph,
  options: LayoutForceOptions,
): LayoutForceResult {
  const { width, height, previous, pinned, previousVirtualSize } = options
  const margin = 20
  const pad = 16 // padding between nodes in area calculation

  // Compute needed space using packing density heuristic
  // Use lower density (more space) for smaller containers to ensure overlap prevention
  let neededArea = 0
  for (const node of graph.nodes) {
    const size = NODE_SIZES[node.kind as keyof typeof NODE_SIZES] || NODE_SIZES.document
    const nodeArea = (size.width + pad) * (size.height + pad)
    neededArea += nodeArea
  }
  // Adaptive packing density: smaller containers get more space buffer
  const containerDiagonal = Math.sqrt(width * width + height * height)
  let packingDensity = 0.40
  if (containerDiagonal < 1000) {
    packingDensity = 0.35 // More space for small containers
  }
  const neededTotal = neededArea / packingDensity

  // Compute virtual layout box dimensions with minimum scale for all sizes
  let virtualWidth = width
  let virtualHeight = height
  const containerArea = width * height
  let scale = Math.max(1, Math.sqrt(neededTotal / containerArea))
  // Always scale up by at least 1.2x to provide extra spacing buffer for force-directed layouts
  scale = Math.max(scale, 1.2)
  virtualWidth = width * scale
  virtualHeight = height * scale

  const innerWidth = virtualWidth - 2 * margin
  const innerHeight = virtualHeight - 2 * margin

  // Deterministic seed for reproducibility
  let seededRandom = 0.5
  const nextRandom = () => {
    seededRandom = (seededRandom * 9301 + 49297) % 233280
    return seededRandom / 233280
  }

  // Initialize nodes with previous positions or layered layout as seed
  const nodes: LayoutForceNode[] = graph.nodes.map(node => {
    let x = margin + innerWidth / 2
    let y = margin + innerHeight / 2

    // Check if node is pinned first
    if (pinned && pinned.has(node.id)) {
      const pinnedPos = pinned.get(node.id)!
      x = pinnedPos.x
      y = pinnedPos.y
    } else if (previous && previous.has(node.id)) {
      // Scale previous position to new virtual box if it was stored from a different virtual size
      let prevPos = previous.get(node.id)!
      if (previousVirtualSize) {
        const widthRatio = virtualWidth / previousVirtualSize.width
        const heightRatio = virtualHeight / previousVirtualSize.height
        prevPos = {
          x: prevPos.x * widthRatio,
          y: prevPos.y * heightRatio,
        }
      }
      x = Math.max(margin, Math.min(innerWidth + margin, prevPos.x))
      y = Math.max(margin, Math.min(innerHeight + margin, prevPos.y))
    } else {
      // Initialize with layered positions based on kind
      const kindPositions: Record<string, number> = {
        document: margin + innerWidth * 0.12,
        question: margin + innerWidth * 0.36,
        decision: margin + innerWidth * 0.62,
        model: margin + innerWidth * 0.88,
      }
      x = kindPositions[node.kind] || (margin + innerWidth / 2)

      // Add deterministic noise to prevent perfect alignment
      x += (nextRandom() - 0.5) * 30
      y = margin + innerHeight / 2 + (nextRandom() - 0.5) * 40
    }

    const simNode: LayoutForceNode = {
      id: node.id,
      kind: node.kind,
      x: Math.max(margin, Math.min(innerWidth + margin, x)),
      y: Math.max(margin, Math.min(innerHeight + margin, y)),
    }

    // Fix pinned nodes so they don't move
    if (pinned && pinned.has(node.id)) {
      simNode.fx = simNode.x
      simNode.fy = simNode.y
    }

    return simNode
  })

  // Create the simulation
  const simulation = d3Force.forceSimulation<LayoutForceNode>(nodes)

  // Force: link attraction (distance by edge kind)
  interface LinkWithDistance extends d3Force.SimulationLinkDatum<LayoutForceNode> {
    distance?: number
  }

  const links: LinkWithDistance[] = graph.edges.map(edge => {
    const sourceNode = nodes.find(n => n.id === edge.source)!
    const targetNode = nodes.find(n => n.id === edge.target)!

    // Distance depends on edge kind
    let distance = 90
    if (edge.kind === 'about') distance = 90
    else if (edge.kind === 'decide') distance = 70
    else if (edge.kind === 'answer-model' || edge.kind === 'laya-classify') distance = 110
    else if (edge.kind === 'ocr-model' || edge.kind === 'vision-fallback') distance = 85

    return {
      source: sourceNode,
      target: targetNode,
      distance,
    }
  })

  simulation.force(
    'link',
    d3Force
      .forceLink<LayoutForceNode, LinkWithDistance>(links)
      .distance((d) => (d.distance as number) || 90)
      .strength(0.6),
  )

  // Force: repulsion (many-body) - stronger repulsion for better spacing
  simulation.force('charge', d3Force.forceManyBody<LayoutForceNode>().strength(-500).distanceMax(700))

  // Force: collision with circles to guarantee no rectangle overlap
  // Scale collision force based on density: denser layouts need stronger collision
  const layoutDensity = graph.nodes.length / (virtualWidth * virtualHeight)
  const collisionStrength = Math.min(1.5, 0.8 + layoutDensity * 1000)
  const collisionIterations = Math.min(8, Math.ceil(4 + layoutDensity * 50))

  simulation.force(
    'collide',
    d3Force
      .forceCollide<LayoutForceNode>()
      .radius(node => {
        const size = NODE_SIZES[node.kind as keyof typeof NODE_SIZES] || NODE_SIZES.document
        const diagonal = Math.sqrt(size.width * size.width + size.height * size.height) / 2
        return diagonal + pad / 2
      })
      .strength(collisionStrength)
      .iterations(collisionIterations),
  )

  // Force: weak horizontal positioning by kind
  const kindX: Record<string, number> = {
    document: margin + innerWidth * 0.12,
    question: margin + innerWidth * 0.36,
    decision: margin + innerWidth * 0.62,
    model: margin + innerWidth * 0.88,
  }
  simulation.force(
    'x',
    d3Force
      .forceX<LayoutForceNode>(node => kindX[node.kind] || margin + innerWidth / 2)
      .strength(0.08),
  )

  // Force: vertical centering (weak)
  simulation.force('y', d3Force.forceY<LayoutForceNode>(margin + innerHeight / 2).strength(0.05))

  // Tune simulation convergence - balanced for performance and quality
  const ticks = Math.min(400, 140 + 5 * graph.nodes.length)
  const alphaDecay = 1 - Math.pow(0.001, 1 / ticks)
  simulation.alphaDecay(alphaDecay).velocityDecay(0.35)

  // Run the simulation synchronously
  const startTime = performance.now()
  for (let i = 0; i < ticks; i++) {
    simulation.tick()

    // Clamp all positions to stay in bounds during simulation
    for (const node of nodes) {
      const size = NODE_SIZES[node.kind as keyof typeof NODE_SIZES] || NODE_SIZES.document
      const minX = margin + size.width / 2
      const maxX = margin + innerWidth - size.width / 2
      const minY = margin + size.height / 2
      const maxY = margin + innerHeight - size.height / 2

      node.x = Math.max(minX, Math.min(maxX, node.x))
      node.y = Math.max(minY, Math.min(maxY, node.y))
    }
  }
  simulation.stop()

  // Post-layout rectangle overlap resolution with sweep algorithm
  // Efficiently resolve overlaps by sorting along x-axis and sweeping
  for (let sweepPass = 0; sweepPass < 200; sweepPass++) {
    const overlaps: Array<{ i: number; j: number; penetration: number }> = []

    // Collect overlapping pairs using sort-by-x sweep for efficiency
    const sortedIndices = nodes.map((_, i) => i).sort((i, j) => {
      const size_i = NODE_SIZES[nodes[i].kind as keyof typeof NODE_SIZES] || NODE_SIZES.document
      const size_j = NODE_SIZES[nodes[j].kind as keyof typeof NODE_SIZES] || NODE_SIZES.document
      return (nodes[i].x - size_i.width / 2) - (nodes[j].x - size_j.width / 2)
    })

    for (let idx = 0; idx < sortedIndices.length; idx++) {
      const i = sortedIndices[idx]
      const node1 = nodes[i]
      const size1 = NODE_SIZES[node1.kind as keyof typeof NODE_SIZES] || NODE_SIZES.document

      // Check only nodes to the right (x-sweep ends when x gap is too large)
      for (let idx2 = idx + 1; idx2 < sortedIndices.length; idx2++) {
        const j = sortedIndices[idx2]
        const node2 = nodes[j]
        const size2 = NODE_SIZES[node2.kind as keyof typeof NODE_SIZES] || NODE_SIZES.document

        const r1x0 = node1.x - size1.width / 2
        const r1y0 = node1.y - size1.height / 2
        const r1x1 = node1.x + size1.width / 2
        const r1y1 = node1.y + size1.height / 2

        const r2x0 = node2.x - size2.width / 2
        const r2y0 = node2.y - size2.height / 2
        const r2x1 = node2.x + size2.width / 2
        const r2y1 = node2.y + size2.height / 2

        // Early exit if x-gap is too large
        if (r2x0 - r1x1 > 0) break

        // Check for actual overlap
        if (r1x0 < r2x1 && r1x1 > r2x0 && r1y0 < r2y1 && r1y1 > r2y0) {
          // Calculate penetration depth
          const overlapLeft = r1x1 - r2x0
          const overlapRight = r2x1 - r1x0
          const overlapTop = r1y1 - r2y0
          const overlapBottom = r2y1 - r1y0
          const penetration = Math.min(overlapLeft, overlapRight, overlapTop, overlapBottom)
          overlaps.push({ i, j, penetration })
        }
      }
    }

    if (overlaps.length === 0) break

    // Sort overlaps by penetration depth (deepest first) to resolve harder cases first
    overlaps.sort((a, b) => b.penetration - a.penetration)

    // Resolve each overlap by separating along minimum penetration axis
    for (const { i, j, penetration } of overlaps) {
      const node1 = nodes[i]
      const node2 = nodes[j]
      const size1 = NODE_SIZES[node1.kind as keyof typeof NODE_SIZES] || NODE_SIZES.document
      const size2 = NODE_SIZES[node2.kind as keyof typeof NODE_SIZES] || NODE_SIZES.document

      const r1x0 = node1.x - size1.width / 2
      const r1y0 = node1.y - size1.height / 2
      const r1x1 = node1.x + size1.width / 2
      const r1y1 = node1.y + size1.height / 2

      const r2x0 = node2.x - size2.width / 2
      const r2y0 = node2.y - size2.height / 2
      const r2x1 = node2.x + size2.width / 2
      const r2y1 = node2.y + size2.height / 2

      const overlapLeft = r1x1 - r2x0
      const overlapRight = r2x1 - r1x0
      const overlapTop = r1y1 - r2y0
      const overlapBottom = r2y1 - r1y0

      const minOverlap = Math.min(overlapLeft, overlapRight, overlapTop, overlapBottom)

      const node1Pinned = node1.fx !== undefined && node1.fx !== null
      const node2Pinned = node2.fx !== undefined && node2.fx !== null

      // Extremely aggressive separation: full penetration + 4px gap
      const push = minOverlap / 2 + 4

      if (minOverlap === overlapLeft || minOverlap === overlapRight) {
        if (!node1Pinned && !node2Pinned) {
          node1.x -= push
          node2.x += push
        } else if (!node1Pinned) {
          node1.x -= minOverlap + 4
        } else if (!node2Pinned) {
          node2.x += minOverlap + 4
        }
      } else {
        if (!node1Pinned && !node2Pinned) {
          node1.y -= push
          node2.y += push
        } else if (!node1Pinned) {
          node1.y -= minOverlap + 4
        } else if (!node2Pinned) {
          node2.y += minOverlap + 4
        }
      }
    }
  }

  // Final clamping to virtual box bounds
  for (const node of nodes) {
    const size = NODE_SIZES[node.kind as keyof typeof NODE_SIZES] || NODE_SIZES.document
    const minX = margin + size.width / 2
    const maxX = margin + innerWidth - size.width / 2
    const minY = margin + size.height / 2
    const maxY = margin + innerHeight - size.height / 2

    node.x = Math.max(minX, Math.min(maxX, node.x))
    node.y = Math.max(minY, Math.min(maxY, node.y))
  }

  const elapsed = performance.now() - startTime

  // Debug: log if >50ms
  if (elapsed > 50) {
    console.debug(`[KG Force] Layout took ${elapsed.toFixed(1)}ms for ${nodes.length} nodes (virtual: ${virtualWidth.toFixed(0)}×${virtualHeight.toFixed(0)})`)
  }

  // Convert to LayoutNode format (top-left position = center - size/2)
  const layoutNodes: LayoutNode[] = nodes.map(simNode => {
    const origNode = graph.nodes.find(n => n.id === simNode.id)!
    const size = NODE_SIZES[origNode.kind as keyof typeof NODE_SIZES] || NODE_SIZES.document

    return {
      ...origNode,
      position: {
        x: simNode.x - size.width / 2,
        y: simNode.y - size.height / 2,
      },
    }
  })

  return {
    nodes: layoutNodes,
    edges: graph.edges,
    virtualWidth,
    virtualHeight,
  }
}

/**
 * Calculate the average degree-centrality positions for a set of nodes.
 * Used for performance analysis in tests.
 */
export function getNodeDegreeCentrality(
  nodeId: string,
  graph: KGraph,
): number {
  const edges = graph.edges.filter(e => e.source === nodeId || e.target === nodeId)
  return edges.length
}
