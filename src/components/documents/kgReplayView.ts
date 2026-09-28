/**
 * Small, always-loaded helpers the graph views use while a Replay runs
 * (camera moves). The replay player + trail builder live in the lazily loaded
 * KgReplayPlayer.tsx / kgReplay.ts.
 */
import type { ReactFlowInstance, Viewport } from '@xyflow/react'

/** What a view highlights for the current replay step. */
export interface ReplayFocus {
  /** Changes on every step (restarts animations / camera). */
  key: string
  /** Nodes to highlight (first = primary). */
  nodes: string[]
  /** Edges leading into the step (flowing dash). */
  edges: string[]
  /** The previous step's nodes (camera context). */
  prevNodes: string[]
  /** Run graph: the replayed run (Layers uses `<id>@<runKey>` for shared nodes). */
  runKey?: string | null
}

export function prefersReducedMotion(): boolean {
  try {
    return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/** Width reserved next to the target for the callout when the stage is wide enough. */
export function calloutReserve(stageWidth: number): number {
  return stageWidth >= 760 ? Math.min(340, Math.round(stageWidth * 0.34)) : 0
}

/**
 * Pan / zoom so `ids` are readable: fit them (plus the previous step's nodes
 * when that still fits at a readable zoom), leave room on the right for the
 * callout, clamp the zoom, animate ~600 ms (instant with reduced motion).
 */
export function flyToNodes(
  rf: Pick<ReactFlowInstance, 'getNode' | 'getNodesBounds' | 'setCenter'>,
  ids: string[],
  prevIds: string[],
  stage: { width: number; height: number },
  opts: { minZoom?: number; maxZoom?: number; duration?: number } = {},
): boolean {
  const present = ids.filter(id => !!rf.getNode(id))
  if (present.length === 0 || stage.width <= 0 || stage.height <= 0) return false
  const minZoom = opts.minZoom ?? 0.45
  const maxZoom = opts.maxZoom ?? 1.15
  const BAR = 56 // the replay bar floats over the top of the stage
  // Two ways to leave room for the callout: beside the targets (wide stages) or
  // below them. Use whichever shows the targets larger.
  const side = calloutReserve(stage.width)
  const below = Math.min(250, Math.round(stage.height * 0.42))
  const modes = [
    ...(side > 0 ? [{ side, below: 0 }] : []),
    { side: 0, below },
  ].map(m => ({
    ...m,
    availW: Math.max(120, stage.width - m.side - 72),
    availH: Math.max(120, stage.height - BAR - m.below - 64),
  }))
  const zoomFor = (b: { width: number; height: number }, m: (typeof modes)[number]) =>
    Math.min(m.availW / Math.max(1, b.width), m.availH / Math.max(1, b.height))
  const current = rf.getNodesBounds(present)
  const withPrev = [...new Set([...present, ...prevIds.filter(id => !!rf.getNode(id))])]
  const union = withPrev.length > present.length ? rf.getNodesBounds(withPrev) : null
  let best: { mode: (typeof modes)[number]; bounds: typeof current; zoom: number } | null = null
  for (const mode of modes) {
    // Include the previous step's nodes (context for the flowing edge) only while it stays readable.
    const bounds = union && zoomFor(union, mode) >= Math.max(minZoom, 0.8) ? union : current
    const z = Math.min(maxZoom, zoomFor(bounds, mode))
    // Side placement wins ties (it keeps the targets vertically centred).
    if (!best || z > best.zoom * 1.12) best = { mode, bounds, zoom: z }
  }
  const { mode, bounds } = best!
  const zoom = Math.max(minZoom, best!.zoom)
  // Targets left of centre (callout on the right), or in the band between the bar and the callout room.
  const cx = bounds.x + bounds.width / 2 + mode.side / 2 / zoom
  const cy = bounds.y + bounds.height / 2 + (mode.below - BAR) / 2 / zoom
  const duration = prefersReducedMotion() ? 0 : opts.duration ?? 600
  void rf.setCenter(cx, cy, { zoom, duration })
  return true
}

/** End of a replay: zoom out smoothly to the whole graph (~800 ms, instant with reduced motion). */
export function flyToOverview(rf: Pick<ReactFlowInstance, 'fitView'> | null | undefined, padding = 0.08) {
  if (!rf) return
  void rf.fitView({ padding, minZoom: 0.1, maxZoom: 1.3, duration: prefersReducedMotion() ? 0 : 800 })
}

/** Restore a saved viewport (animated unless reduced motion). */
export function restoreViewport(rf: Pick<ReactFlowInstance, 'setViewport'> | null | undefined, vp: Viewport | null | undefined) {
  if (!rf || !vp) return
  void rf.setViewport(vp, { duration: prefersReducedMotion() ? 0 : 400 })
}
