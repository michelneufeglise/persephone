import React from 'react'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  useInternalNode,
  type Edge,
  type EdgeProps,
  type Position,
} from '@xyflow/react'

/**
 * Floating edge component for React Flow v12.
 * Computes intersection points between node bounding boxes and the center-to-center line,
 * allowing edges to attach dynamically regardless of node direction.
 */

function getNodeIntersection(
  node: any,
  intersectionPoint: { x: number; y: number },
): { x: number; y: number } {
  const nx = Number(node.internals.positionAbsolute.x)
  const ny = Number(node.internals.positionAbsolute.y)
  const nw = Number(node.measured?.width || 0)
  const nh = Number(node.measured?.height || 0)

  const px = intersectionPoint.x
  const py = intersectionPoint.y

  // Find which edge of the rectangle the line intersects
  const edges = [
    { x: nx, y: ny + nh / 2, ex: nx, ey: ny }, // left
    { x: nx + nw, y: ny + nh / 2, ex: nx + nw, ey: ny }, // right
    { x: nx + nw / 2, y: ny, ex: nx, ey: ny }, // top
    { x: nx + nw / 2, y: ny + nh, ex: nx, ey: ny + nh }, // bottom
  ]

  let closest = edges[0]
  let minDist = Infinity

  for (const edge of edges) {
    const dist = Math.hypot(px - edge.x, py - edge.y)
    if (dist < minDist) {
      minDist = dist
      closest = edge
    }
  }

  return { x: closest.x, y: closest.y }
}

function getEdgePosition(
  node: any,
  intersectionPoint: { x: number; y: number },
): 'top' | 'right' | 'bottom' | 'left' {
  const nx = Number(node.internals.positionAbsolute.x)
  const ny = Number(node.internals.positionAbsolute.y)
  const nw = Number(node.measured?.width || 0)
  const nh = Number(node.measured?.height || 0)

  const cx = nx + nw / 2
  const cy = ny + nh / 2

  const px = intersectionPoint.x
  const py = intersectionPoint.y

  const dx = px - cx
  const dy = py - cy
  const angle = Math.atan2(dy, dx)

  // Map angle to edge position
  if (angle > -Math.PI / 4 && angle <= Math.PI / 4) return 'right'
  if (angle > Math.PI / 4 && angle <= (3 * Math.PI) / 4) return 'bottom'
  if (angle > (3 * Math.PI) / 4 || angle <= -(3 * Math.PI) / 4) return 'left'
  return 'top'
}

export function FloatingEdge(props: EdgeProps) {
  const { source, target, markerEnd, label, animated, style } = props
  const titleAttr = (props as any).title as string | undefined

  const sourceNode = useInternalNode(source)
  const targetNode = useInternalNode(target)

  if (!sourceNode || !targetNode) {
    return null
  }

  const sourceCenter = {
    x: Number(sourceNode.internals.positionAbsolute.x) + Number(sourceNode.measured?.width || 0) / 2,
    y: Number(sourceNode.internals.positionAbsolute.y) + Number(sourceNode.measured?.height || 0) / 2,
  }

  const targetCenter = {
    x: Number(targetNode.internals.positionAbsolute.x) + Number(targetNode.measured?.width || 0) / 2,
    y: Number(targetNode.internals.positionAbsolute.y) + Number(targetNode.measured?.height || 0) / 2,
  }

  const sourcePos = getNodeIntersection(sourceNode, targetCenter)
  const targetPos = getNodeIntersection(targetNode, sourceCenter)

  const sourcePosition = getEdgePosition(sourceNode, targetCenter) as Position
  const targetPosition = getEdgePosition(targetNode, sourceCenter) as Position

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX: sourcePos.x,
    sourceY: sourcePos.y,
    targetX: targetPos.x,
    targetY: targetPos.y,
  })

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      {label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              fontSize: 10,
              fontFamily: 'var(--font-family-body)',
              pointerEvents: 'none',
            }}
            className="px-2.5 py-1 rounded-full bg-[var(--bg-glass-strong)] border border-[var(--border-glass)] text-[var(--text-secondary)]"
            title={titleAttr}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
