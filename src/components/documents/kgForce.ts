import * as d3Force from 'd3-force'
import { NODE_SIZES } from './kgModel'
import type { KGraph, LayoutNode, LayoutEdge } from './kgModel'

export interface LayoutForceOptions {
  width: number
  height: number
  previous?: Map<string, { x: number; y: number }>
  pinned?: Map<string, { x: number; y: number }>
  previousVirtualSize?: { width: number; height: number }
  /** Per-node size override (defaults to NODE_SIZES by kind). */
  sizeOf?: (node: KGraph['nodes'][number]) => { width: number; height: number }
  /** Uniform link distance (defaults to per-edge-kind distances). */
  linkDistance?: number
  /** Many-body strength (default -500). */
  charge?: number
  /**
   * Hub node ids: pinned at the centre of the layout box (several hubs are spread
   * horizontally); all other nodes are seeded on a ring around them. Default none.
   */
  hubIds?: string[]
}

export interface LayoutForceNode {
  id: string
  kind: 'document' | 'question' | 'decision' | 'model' | 'answer' | 'planner' | 'web' | 'profile' | 'entity' | 'pipeline'
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
  const { width, height, previous, previousVirtualSize, sizeOf, linkDistance, hubIds } = options
  let { pinned } = options
  const chargeStrength = options.charge ?? -500
  const margin = 20
  const pad = 16 // padding between nodes in area calculation

  const sizeById = new Map<string, { width: number; height: number }>()
  for (const node of graph.nodes) {
    sizeById.set(
      node.id,
      sizeOf?.(node) || NODE_SIZES[node.kind as keyof typeof NODE_SIZES] || NODE_SIZES.document,
    )
  }
  const sz = (node: { id: string }) => sizeById.get(node.id) || NODE_SIZES.document

  // Compute needed space using packing density heuristic
  // Use lower density (more space) for smaller containers to ensure overlap prevention
  let neededArea = 0
  for (const node of graph.nodes) {
    const size = sz(node)
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

  // Hub-and-spoke seeding: pin hubs at the centre, seed everything else on rings.
  const seeds = new Map<string, { x: number; y: number }>()
  const hubSet = new Set((hubIds || []).filter(id => sizeById.has(id)))
  if (hubSet.size > 0) {
    const cx = margin + innerWidth / 2
    const cy = margin + innerHeight / 2
    const hubs = graph.nodes.filter(n => hubSet.has(n.id))
    const gap = 60
    const totalW = hubs.reduce((acc, n) => acc + sz(n).width, 0) + gap * (hubs.length - 1)
    const hubPins = new Map(pinned || [])
    let hx = cx - totalW / 2
    for (const hub of hubs) {
      const w = sz(hub).width
      if (!hubPins.has(hub.id)) hubPins.set(hub.id, { x: hx + w / 2, y: cy })
      hx += w + gap
    }
    pinned = hubPins

    const hubNeighbours = new Set<string>()
    for (const e of graph.edges) {
      if (hubSet.has(e.source) && !hubSet.has(e.target)) hubNeighbours.add(e.target)
      if (hubSet.has(e.target) && !hubSet.has(e.source)) hubNeighbours.add(e.source)
    }
    const ring1 = graph.nodes.filter(n => hubNeighbours.has(n.id))
    const ring2 = graph.nodes.filter(n => !hubSet.has(n.id) && !hubNeighbours.has(n.id))
    const r1 = (linkDistance ?? 200) + totalW / 2 - sz(hubs[0]).width / 2
    const place = (ring: typeof ring1, radius: number, offset: number) => {
      ring.forEach((n, i) => {
        const a = -Math.PI / 2 + offset + (2 * Math.PI * i) / Math.max(1, ring.length)
        seeds.set(n.id, { x: cx + radius * Math.cos(a), y: cy + radius * Math.sin(a) })
      })
    }
    place(ring1, r1, 0)
    place(ring2, r1 * 1.7, ring1.length > 0 ? Math.PI / Math.max(2, ring1.length) : 0)
  }

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
    } else if (seeds.has(node.id)) {
      const seed = seeds.get(node.id)!
      x = seed.x
      y = seed.y
    } else {
      // Initialize with layered positions based on kind
      const kindPositions: Record<string, number> = {
        document: margin + innerWidth * 0.08,
        question: margin + innerWidth * 0.25,
        decision: margin + innerWidth * 0.42,
        planner: margin + innerWidth * 0.58,
        web: margin + innerWidth * 0.72,
        profile: margin + innerWidth * 0.85,
        model: margin + innerWidth * 0.95,
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

  // Build a map of id → node for efficient lookups
  const nodeMap = new Map(nodes.map(n => [n.id, n]))

  // Filter edges to only those with valid endpoints, warn about dropped edges
  const droppedEdges: Array<{ id: string; source: string; target: string }> = []
  const links: LinkWithDistance[] = []

  for (const edge of graph.edges) {
    const sourceNode = nodeMap.get(edge.source)
    const targetNode = nodeMap.get(edge.target)

    if (!sourceNode || !targetNode) {
      droppedEdges.push({ id: edge.id, source: edge.source, target: edge.target })
      continue
    }

    // Distance depends on edge kind
    let distance = 90
    if (linkDistance !== undefined) distance = linkDistance
    else if (edge.kind === 'about') distance = 90
    else if (edge.kind === 'decide') distance = 70
    else if (edge.kind === 'answer-model' || edge.kind === 'laya-classify') distance = 110
    else if (edge.kind === 'ocr-model' || edge.kind === 'vision-fallback') distance = 85

    links.push({
      source: sourceNode,
      target: targetNode,
      distance,
    })
  }

  if (droppedEdges.length > 0) {
    console.warn('[KG Force] dropped edges with missing endpoints', droppedEdges.map(e => ({ id: e.id, source: e.source, target: e.target })))
  }

  simulation.force(
    'link',
    d3Force
      .forceLink<LayoutForceNode, LinkWithDistance>(links)
      .distance((d) => (d.distance as number) || 90)
      .strength(0.6),
  )

  // Force: repulsion (many-body) - stronger repulsion for better spacing
  simulation.force('charge', d3Force.forceManyBody<LayoutForceNode>().strength(chargeStrength).distanceMax(700))

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
        const size = sz(node)
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
      const size = sz(node)
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
      const size_i = sz(nodes[i])
      const size_j = sz(nodes[j])
      return (nodes[i].x - size_i.width / 2) - (nodes[j].x - size_j.width / 2)
    })

    for (let idx = 0; idx < sortedIndices.length; idx++) {
      const i = sortedIndices[idx]
      const node1 = nodes[i]
      const size1 = sz(node1)

      // Check only nodes to the right (x-sweep ends when x gap is too large)
      for (let idx2 = idx + 1; idx2 < sortedIndices.length; idx2++) {
        const j = sortedIndices[idx2]
        const node2 = nodes[j]
        const size2 = sz(node2)

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
      const size1 = sz(node1)
      const size2 = sz(node2)

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
    const size = sz(node)
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
    const size = sz(origNode)

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
