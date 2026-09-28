/**
 * Entities-view edge: a straight segment between the two cards' borders (as
 * FloatingStraightEdge), a wide invisible hit path, and an HTML label pill
 * placed at a precomputed fraction `t` of the segment (see placeEdgeLabels) so
 * labels don't collide. Verification relations (owns, signed, …) always show
 * their label + score; secondary ones (mentioned_in, candidates) and the
 * relations aggregated under an edge show on hover / selection.
 */
import { BaseEdge, EdgeLabelRenderer, getStraightPath, useInternalNode, type EdgeProps } from '@xyflow/react'
import { clsx } from 'clsx'
import { borderPointOf } from './kgEntities'

export interface EntityEdgeRenderData {
  type: string
  label: string
  score: number | null
  band: string | null
  primary: boolean
  verification: boolean
  others: { id: string; type: string; label: string }[]
  /** Label position along the segment (0 = source border, 1 = target border). */
  t?: number
  hover?: boolean
  selected?: boolean
  dim?: boolean
  onSelect?: (relationId: string) => void
  relationId: string
  [key: string]: unknown
}

function nodeBox(node: any): { cx: number; cy: number; hw: number; hh: number } {
  const x = Number(node.internals.positionAbsolute.x)
  const y = Number(node.internals.positionAbsolute.y)
  const w = Number(node.measured?.width || node.width || node.style?.width || 0)
  const h = Number(node.measured?.height || node.height || node.style?.height || 0)
  return { cx: x + w / 2, cy: y + h / 2, hw: w / 2, hh: h / 2 }
}

/** Colour of a signature band (score chip, signed edge). */
export function bandColor(band: string | null | undefined): string {
  if (band === 'consistent') return 'rgb(16 185 129)'
  if (band === 'inconclusive') return 'rgb(245 158 11)'
  if (band === 'inconsistent') return 'rgb(239 68 68)'
  return '#a78bfa'
}

export function EntityEdge({ id, source, target, markerEnd, style, data, interactionWidth }: EdgeProps) {
  const sourceNode = useInternalNode(source)
  const targetNode = useInternalNode(target)
  if (!sourceNode || !targetNode) return null
  const d = (data || {}) as EntityEdgeRenderData

  const a = nodeBox(sourceNode)
  const b = nodeBox(targetNode)
  const s = borderPointOf(a, b.cx, b.cy)
  const t = borderPointOf(b, a.cx, a.cy, 5) // room for the arrow head
  const [path] = getStraightPath({ sourceX: s.x, sourceY: s.y, targetX: t.x, targetY: t.y })
  const f = typeof d.t === 'number' ? d.t : 0.5
  const lx = s.x + (t.x - s.x) * f
  const ly = s.y + (t.y - s.y) * f

  const expanded = !!d.hover || !!d.selected
  const showLabel = d.primary || (expanded && !d.dim)
  const sc = d.score != null ? bandColor(d.band) : null

  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} interactionWidth={interactionWidth ?? 24} />
      {showLabel && (
        <EdgeLabelRenderer>
          <div
            className={clsx('kg-ent-label nodrag nopan', d.primary ? 'is-primary' : 'is-secondary', d.selected && 'is-selected', d.dim && !expanded && 'is-dim')}
            style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)` }}
            onClick={e => {
              e.stopPropagation()
              d.onSelect?.(d.relationId)
            }}
            title={[d.label, ...d.others.map(o => o.label)].join(' · ') + ' — click for evidence'}
          >
            <span>{d.label}</span>
            {sc && (
              <span className="kg-ent-label__score" style={{ color: sc, background: `color-mix(in oklab, ${sc} 16%, transparent)` }}>
                {d.score}%
              </span>
            )}
            {d.others.length > 0 &&
              (expanded ? (
                <span className="kg-ent-label__more">+ {d.others.map(o => o.label).join(', ')}</span>
              ) : (
                <span className="kg-ent-label__more">+{d.others.length}</span>
              ))}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}
