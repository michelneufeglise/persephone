/**
 * Edge for the structured Network (ELK routes) and Layers (lane routes) views:
 * an orthogonal polyline with rounded corners through precomputed points, a
 * small arrowhead, and a label pill shown on hover or when an endpoint is
 * selected. Colours come from `.kg-network` rules in index.css (the paths
 * deliberately avoid the `react-flow__edge-path` class, which flows.css forces
 * to the accent colour).
 */
import { useState } from 'react'
import { EdgeLabelRenderer, type EdgeProps } from '@xyflow/react'
import { clsx } from 'clsx'
import { roundedPolylinePath, polylineLabelPoint, type Pt } from './kgLayout'
import type { EdgeCategory } from './kgNetwork'

export interface RunEdgeData {
  points?: Pt[]
  category: EdgeCategory
  label?: string
  title?: string
  active?: boolean
  dim?: boolean
  failed?: boolean
  showLabel?: boolean
  [key: string]: unknown
}

const ARROW_LEN = 6
const ARROW_HALF = 3

export function RunGraphEdge({ id, data, sourceX, sourceY, targetX, targetY }: EdgeProps) {
  const [hover, setHover] = useState(false)
  const d = (data || {}) as RunEdgeData
  const raw: Pt[] =
    Array.isArray(d.points) && d.points.length >= 2
      ? d.points
      : [
          { x: sourceX, y: sourceY },
          { x: targetX, y: targetY },
        ]
  // Stop the line at the arrow's base so the stroke doesn't poke through the tip.
  const tip = raw[raw.length - 1]
  const prev = raw[raw.length - 2]
  const len = Math.hypot(tip.x - prev.x, tip.y - prev.y) || 1
  const ux = (tip.x - prev.x) / len
  const uy = (tip.y - prev.y) / len
  const base = { x: tip.x - ux * ARROW_LEN, y: tip.y - uy * ARROW_LEN }
  const pts = len > ARROW_LEN ? [...raw.slice(0, -1), base] : raw
  const path = roundedPolylinePath(pts, 8)
  const arrow = `M ${tip.x} ${tip.y} L ${base.x - uy * ARROW_HALF} ${base.y + ux * ARROW_HALF} L ${base.x + uy * ARROW_HALF} ${base.y - ux * ARROW_HALF} Z`
  const labelPt = polylineLabelPoint(raw)
  const showLabel = !!d.label && (hover || !!d.showLabel) && !d.dim

  return (
    <g
      className={clsx(
        'kg-net-edge',
        `kg-net-edge--${d.category || 'other'}`,
        d.active && 'is-active',
        d.dim && 'is-dim',
        d.failed && 'is-failed',
        hover && 'is-hover',
      )}
    >
      <path id={id} d={path} className="kg-net-edge__path" fill="none" />
      <path d={arrow} className="kg-net-edge__arrow" />
      <path
        d={path}
        fill="none"
        stroke="transparent"
        strokeWidth={14}
        style={{ pointerEvents: 'stroke' }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
      >
        {d.title || d.label ? <title>{d.title ? `${d.label} — ${d.title}` : d.label}</title> : null}
      </path>
      {showLabel && (
        <EdgeLabelRenderer>
          <div
            className="kg-net-edge__label nodrag nopan"
            style={{ transform: `translate(-50%, -50%) translate(${labelPt.x}px, ${labelPt.y}px)` }}
          >
            {d.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </g>
  )
}

export const runEdgeTypes = { runEdge: RunGraphEdge }
