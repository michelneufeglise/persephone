/**
 * Entities view of the knowledge graph: turns the persisted knowledge store
 * (fetchKnowledgeGraph → entities + relations) into a KGraph and lays it out as a
 * hub-and-spoke force layout around the person node(s).
 *
 * Pure (no React / React Flow imports) so it can be bundled and checked in Node.
 */
import { layoutForce } from './kgForce'
import { entityNodeSize } from './kgModel'
import type { KGraph, KNode, KEdge } from './kgModel'
import type { LayoutForceResult } from './kgForce'
import { SOCIAL_PLATFORMS, resolvePlatform } from './socialPlatforms'
import type { SocialPlatform } from './socialPlatforms'
import { friendlyDocNames } from './kgFormat'

interface StoreEntity {
  id: string
  type: string
  name: string
  props?: Record<string, unknown> | null
  mention_count?: number | null
}

interface StoreRelation {
  id: string
  src: string
  dst: string
  type: string
  confidence?: number | null
  source?: string | null
  props?: Record<string, unknown> | null
}

export interface EntityRelationView {
  id: string
  type: string
  label: string
  direction: 'out' | 'in'
  otherId: string
  otherName: string
  /** Signature score (signed / verified_against), when stored. */
  score?: number | null
  band?: string | null
}

/** Relations that carry a verifiable claim — always labelled, drawn heavier. */
export const VERIFICATION_RELATIONS: ReadonlySet<string> = new Set(['owns', 'signed', 'verified_against', 'signature_specimen', 'works_at'])
/** Relations whose label is always shown (the rest — mentioned_in, candidate_profile — on hover / selection). */
export const PRIMARY_RELATIONS: ReadonlySet<string> = new Set([
  'owns',
  'signed',
  'verified_against',
  'signature_specimen',
  'works_at',
  'has_role',
  'likely_profile',
  'located_in',
])
/** Which relation represents a pair of nodes when several connect them (highest wins). */
const REL_PRIORITY: Record<string, number> = {
  signed: 10,
  verified_against: 9,
  signature_specimen: 8,
  owns: 7,
  works_at: 6,
  has_role: 5,
  likely_profile: 4,
  located_in: 3,
  candidate_profile: 2,
  mentioned_in: 1,
}

/** Data carried by an Entities-view edge (one per connected node pair). */
export interface EntityEdgeData {
  /** Relation shown for the pair (its id is the edge id). */
  relationId: string
  type: string
  label: string
  score: number | null
  band: string | null
  primary: boolean
  verification: boolean
  /** Other relations between the same two nodes (e.g. mentioned_in under signed). */
  others: { id: string; type: string; label: string }[]
  [key: string]: unknown
}

export type EntityMatch = 'likely' | 'candidate' | null

/** Layout tuning for the Entities view (Network keeps layoutForce defaults). */
export const ENTITY_LAYOUT = { linkDistance: 250, charge: -1400 }

/** "works_at" → "works at" */
export function relationLabel(type: string): string {
  return String(type || '').replace(/_/g, ' ')
}

/**
 * Strip platform suffixes (" | LinkedIn", " - Facebook", " • Instagram photos and videos",
 * " / X", " | Twitter") and truncate long profile titles.
 */
export function cleanProfileTitle(name: string, max = 70): string {
  let t = String(name || '').trim()
  t = t
    .replace(/\s*[|\-–—·•/]\s*(?:LinkedIn|Facebook|Instagram(?:\s+photos\s+and\s+videos)?|Twitter|X)\s*$/i, '')
    .trim()
  if (t.length > max) t = t.slice(0, max - 1).trimEnd() + '…'
  return t || String(name || '')
}

/** Host for a profile: props.host, else the URL hostname (without www.). */
export function profileHost(props: Record<string, unknown> | null | undefined): string {
  const host = props && typeof props.host === 'string' ? props.host : ''
  if (host) return host.replace(/^www\./, '')
  const url = props && typeof props.url === 'string' ? props.url : ''
  if (!url) return ''
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

/** Social platform of a profile entity: props.platform, else derived from props.url. */
export function profilePlatform(props: Record<string, unknown> | null | undefined): SocialPlatform | null {
  const url = props && typeof props.url === 'string' ? props.url : ''
  return resolvePlatform(props?.platform, url)
}

/** Build the entity KGraph (node data carries everything the node + detail card need). */
export function buildEntityGraph(
  kg: { entities: StoreEntity[]; relations: StoreRelation[] },
  conversationId = '',
): KGraph {
  const byId = new Map(kg.entities.map(e => [e.id, e]))
  const relations = (kg.relations || []).filter(r => byId.has(r.src) && byId.has(r.dst))

  const likely = new Set(relations.filter(r => r.type === 'likely_profile').map(r => r.dst))
  const candidate = new Set(relations.filter(r => r.type === 'candidate_profile').map(r => r.dst))

  // Friendly document names ("Handwritten letter"), the people in scope stripped from file names.
  const personNames = kg.entities.filter(e => e.type === 'person').map(e => e.name)
  const docNames = friendlyDocNames(
    kg.entities
      .filter(e => e.type === 'document')
      .map(e => {
        const p = (e.props || {}) as Record<string, unknown>
        return {
          id: e.id,
          filename: typeof p.filename === 'string' && p.filename ? p.filename : e.name,
          hints: { kind: typeof p.kind === 'string' ? p.kind : null, signatureCard: !!p.signature_card, ownerNames: personNames },
        }
      }),
  )
  const shownName = (id: string) => docNames.get(id) ?? byId.get(id)!.name
  const scoreOf = (r: StoreRelation): number | null => {
    const v = (r.props || {})['score']
    return typeof v === 'number' && Number.isFinite(v) ? v : null
  }
  const bandOf = (r: StoreRelation): string | null => {
    const v = (r.props || {})['band']
    return typeof v === 'string' && v ? v : null
  }

  const nodes: KNode[] = kg.entities.map(entity => {
    const props = (entity.props || {}) as Record<string, unknown>
    const isProfile = entity.type === 'profile'
    const rels: EntityRelationView[] = []
    for (const r of relations) {
      if (r.src === entity.id) {
        rels.push({ id: r.id, type: r.type, label: relationLabel(r.type), direction: 'out', otherId: r.dst, otherName: shownName(r.dst), score: scoreOf(r), band: bandOf(r) })
      } else if (r.dst === entity.id) {
        rels.push({ id: r.id, type: r.type, label: relationLabel(r.type), direction: 'in', otherId: r.src, otherName: shownName(r.src), score: scoreOf(r), band: bandOf(r) })
      }
    }
    // Verification relations first, then the rest (stable within a group)
    rels.sort((a, b) => (REL_PRIORITY[b.type] || 0) - (REL_PRIORITY[a.type] || 0))
    const match: EntityMatch = likely.has(entity.id) ? 'likely' : candidate.has(entity.id) ? 'candidate' : null
    const platform = isProfile ? profilePlatform(props) : null
    return {
      id: entity.id,
      kind: 'entity',
      label: entity.name,
      data: {
        entityId: entity.id,
        entityType: entity.type,
        type: entity.type,
        fullName: entity.name,
        displayName: isProfile ? cleanProfileTitle(entity.name) : entity.type === 'document' ? shownName(entity.id) : entity.name,
        host: isProfile ? profileHost(props) : '',
        platform,
        platformLabel: platform ? SOCIAL_PLATFORMS[platform].label : isProfile ? 'Web' : '',
        mention_count: Number(entity.mention_count) || 0,
        props,
        relations: rels,
        match,
      },
      conversationId,
      runIds: [],
      messageIds: [],
    }
  })

  // One edge per connected pair: the highest-priority relation represents it,
  // the others ride along (shown on hover / in the evidence panel).
  const groups = new Map<string, StoreRelation[]>()
  for (const r of relations) {
    const key = r.src < r.dst ? `${r.src}\u0000${r.dst}` : `${r.dst}\u0000${r.src}`
    const g = groups.get(key)
    if (g) g.push(r)
    else groups.set(key, [r])
  }
  const edges: KEdge[] = []
  for (const group of groups.values()) {
    const sorted = [...group].sort((a, b) => (REL_PRIORITY[b.type] || 0) - (REL_PRIORITY[a.type] || 0))
    const r = sorted[0]
    const score = scoreOf(r)
    const withScore = (r.type === 'signed' || r.type === 'verified_against') && score != null
    const data: EntityEdgeData = {
      relationId: r.id,
      type: r.type,
      label: relationLabel(r.type),
      score: withScore ? score : null,
      band: bandOf(r),
      primary: PRIMARY_RELATIONS.has(r.type),
      verification: VERIFICATION_RELATIONS.has(r.type),
      others: sorted.slice(1).map(o => ({ id: o.id, type: o.type, label: relationLabel(o.type) })),
    }
    edges.push({
      id: r.id,
      source: r.src,
      target: r.dst,
      label: withScore ? `${relationLabel(r.type)} · ${score}%` : relationLabel(r.type),
      kind: r.type,
      data,
    })
  }

  return { nodes, edges }
}

/** Size of an entity KNode (person nodes are a bit larger). */
export function entitySizeOf(node: KNode): { width: number; height: number } {
  return entityNodeSize((node.data?.entityType as string) || null)
}

/** Force layout for the Entities view: person node(s) pinned at the centre, others on spokes. */
export function layoutEntities(graph: KGraph, size: { width: number; height: number }): LayoutForceResult {
  let hubIds = graph.nodes.filter(n => n.data?.entityType === 'person').map(n => n.id)
  if (hubIds.length === 0 && graph.nodes.length > 1) {
    // No person: use the most-connected entity as the hub.
    const degree = new Map<string, number>()
    for (const e of graph.edges) {
      degree.set(e.source, (degree.get(e.source) || 0) + 1)
      degree.set(e.target, (degree.get(e.target) || 0) + 1)
    }
    const top = [...graph.nodes].sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0))[0]
    if (top && (degree.get(top.id) || 0) > 0) hubIds = [top.id]
  }
  return layoutForce(graph, {
    width: size.width,
    height: size.height,
    sizeOf: entitySizeOf,
    linkDistance: ENTITY_LAYOUT.linkDistance,
    charge: ENTITY_LAYOUT.charge,
    hubIds,
  })
}

// ── Person-centred layout ────────────────────────────────────────────────────

/** Above this many entities the Entities view keeps the force layout. */
export const FOCAL_LAYOUT_MAX_NODES = 60

/** Screen angle (degrees, 0 = right, 90 = down) of each entity type's sector around the focal person. */
const SECTOR_ANGLE: Record<string, number> = {
  document: 0,
  organization: 168,
  role: 212,
  location: 128,
  profile: 270,
  person: 90,
}

/** Most-connected person node of an entity graph (ties: mentions, then id); null when none is connected. */
export function focalEntityId(graph: KGraph): string | null {
  const degree = new Map<string, number>()
  for (const e of graph.edges) {
    const w = 1 + (Array.isArray((e.data as EntityEdgeData | undefined)?.others) ? (e.data as EntityEdgeData).others.length : 0)
    degree.set(e.source, (degree.get(e.source) || 0) + w)
    degree.set(e.target, (degree.get(e.target) || 0) + w)
  }
  let best: KNode | null = null
  for (const n of graph.nodes) {
    if (n.data?.entityType !== 'person') continue
    const d = degree.get(n.id) || 0
    if (d === 0) continue
    const bd = best ? degree.get(best.id) || 0 : -1
    const nm = Number(n.data?.mention_count) || 0
    const bm = best ? Number(best.data?.mention_count) || 0 : -1
    if (!best || d > bd || (d === bd && (nm > bm || (nm === bm && n.id < best.id)))) best = n
  }
  return best ? best.id : null
}

/**
 * Person-centred layout: the focal person in the middle, organisations / roles
 * on the left, documents on an arc to the right (letters + signature cards on
 * top, documents tied to an organisation at the bottom so their edges pass
 * under the person), profiles above, everything else one ring further out.
 * Deterministic; a final push-apart pass removes any overlap. Returns null when
 * there is no focal person or the graph is too large (caller falls back to the
 * force layout).
 */
export function layoutEntitiesFocal(graph: KGraph, size: { width: number; height: number }): LayoutForceResult | null {
  if (graph.nodes.length < 2 || graph.nodes.length > FOCAL_LAYOUT_MAX_NODES) return null
  const focalId = focalEntityId(graph)
  if (!focalId) return null
  const byId = new Map(graph.nodes.map(n => [n.id, n]))
  const sizeOf = (id: string) => entitySizeOf(byId.get(id)!)
  const typeOf = (id: string) => String(byId.get(id)?.data?.entityType || 'other')

  const adj = new Map<string, { other: string; type: string; out: boolean }[]>()
  const link = (a: string, b: string, type: string, out: boolean) => {
    const l = adj.get(a)
    if (l) l.push({ other: b, type, out })
    else adj.set(a, [{ other: b, type, out }])
  }
  for (const e of graph.edges) {
    const types = [e.kind, ...(((e.data as EntityEdgeData | undefined)?.others || []).map(o => o.type))]
    for (const t of types) {
      link(e.source, e.target, t, true)
      link(e.target, e.source, t, false)
    }
  }
  const neighbours = (id: string) => adj.get(id) || []

  // Ring 1: direct neighbours of the focal person, grouped by sector.
  const ring1 = [...new Set(neighbours(focalId).map(n => n.other))].filter(id => id !== focalId)
  const sectors = new Map<string, string[]>()
  for (const id of ring1) {
    const t = typeOf(id)
    const key = t in SECTOR_ANGLE ? t : 'person'
    const l = sectors.get(key)
    if (l) l.push(id)
    else sectors.set(key, [id])
  }
  // Documents: signed letters at the top, signature cards next, organisation-linked ones at the bottom.
  const docScore = (id: string) => {
    const ns = neighbours(id)
    let s = 0
    if (ns.some(n => n.other === focalId && n.type === 'signed')) s -= 3
    if (ns.some(n => n.type === 'verified_against' && n.out)) s -= 2
    if (ns.some(n => n.type === 'signature_specimen') || byId.get(id)?.data?.props && (byId.get(id)!.data.props as Record<string, unknown>).signature_card) s -= 1
    if (ns.some(n => typeOf(n.other) === 'organization')) s += 2
    return s
  }
  const docs = sectors.get('document')
  if (docs) docs.sort((a, b) => docScore(a) - docScore(b) || String(byId.get(a)!.label).localeCompare(String(byId.get(b)!.label)))
  for (const [key, ids] of sectors) if (key !== 'document') ids.sort((a, b) => String(byId.get(a)!.label).localeCompare(String(byId.get(b)!.label)))

  const fs = sizeOf(focalId)
  let maxW = 0
  let maxH = 0
  for (const id of ring1) {
    const s = sizeOf(id)
    maxW = Math.max(maxW, s.width)
    maxH = Math.max(maxH, s.height)
  }
  // Horizontal gap wide enough for the always-shown labels on the focal
  // person's edges ("signature specimen +1", "signed 96% +1") to sit between
  // the cards instead of on top of them.
  let labelGap = 100
  for (const e of graph.edges) {
    if (e.source !== focalId && e.target !== focalId) continue
    const d = e.data as EntityEdgeData | undefined
    if (!d || !d.primary) continue
    labelGap = Math.max(labelGap, edgeLabelSize(d.label, d.score != null, d.others?.length ? 22 : 0).width + 36)
  }
  const rx = fs.width / 2 + maxW / 2 + Math.min(labelGap, 210)
  const ry = fs.height / 2 + maxH / 2 + 110
  const centres = new Map<string, { x: number; y: number }>()
  centres.set(focalId, { x: 0, y: 0 })
  const angleOf = new Map<string, number>()
  for (const [key, ids] of sectors) {
    const base = SECTOR_ANGLE[key] ?? 90
    const k = ids.length
    // Documents spread over up to ±70°, other sectors in 30° steps.
    const step = key === 'document' ? Math.min(52, 140 / Math.max(1, k - 1)) : 30
    // Stretch the ellipse when the document arc is crowded so cards keep their distance.
    let stretch = 1
    if (key === 'document' && k > 1) {
      const half = ((step * (k - 1)) / 2) * (Math.PI / 180)
      const available = 2 * ry * Math.sin(half)
      const needed = (k - 1) * (maxH + 36)
      if (available > 0) stretch = Math.min(1.9, Math.max(1, needed / available))
    }
    // A lone document tied to an organisation drops below the person, so the
    // organisation → document edge passes under the person card, not through it.
    const orgLinked = (id: string) => neighbours(id).some(n => typeOf(n.other) === 'organization')
    const bias = key === 'document' && k === 1 && orgLinked(ids[0]) ? 32 : key === 'document' && k === 2 && orgLinked(ids[1]) ? 14 : 0
    ids.forEach((id, i) => {
      const deg = base + (i - (k - 1) / 2) * step + bias
      const rad = (deg * Math.PI) / 180
      angleOf.set(id, deg)
      centres.set(id, { x: Math.cos(rad) * rx * stretch, y: Math.sin(rad) * ry * stretch })
    })
  }

  // Ring 2+: breadth-first outward from the placed neighbour, along its angle.
  const queue = [...ring1]
  const childCount = new Map<string, number>()
  while (queue.length) {
    const pid = queue.shift()!
    const pa = angleOf.get(pid) ?? 90
    const pc = centres.get(pid)!
    for (const n of neighbours(pid)) {
      if (centres.has(n.other)) continue
      const idx = childCount.get(pid) || 0
      childCount.set(pid, idx + 1)
      const deg = pa + (idx % 2 === 0 ? 1 : -1) * Math.ceil(idx / 2) * 22
      const rad = (deg * Math.PI) / 180
      const s = sizeOf(n.other)
      const dist = Math.max(s.width, sizeOf(pid).width) / 2 + 140
      centres.set(n.other, { x: pc.x + Math.cos(rad) * dist * 1.25, y: pc.y + Math.sin(rad) * dist * 0.75 })
      angleOf.set(n.other, deg)
      queue.push(n.other)
    }
  }
  // Disconnected leftovers: a row under everything.
  let maxY = 0
  for (const c of centres.values()) maxY = Math.max(maxY, c.y)
  let rowX = -rx
  for (const n of graph.nodes) {
    if (centres.has(n.id)) continue
    const s = sizeOf(n.id)
    centres.set(n.id, { x: rowX + s.width / 2, y: maxY + ry })
    rowX += s.width + 40
  }

  // Push-apart: resolve overlaps (the focal person stays put).
  const ids = graph.nodes.map(n => n.id)
  const GAP = 28
  for (let iter = 0; iter < 80; iter++) {
    let moved = false
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = centres.get(ids[i])!
        const b = centres.get(ids[j])!
        const sa = sizeOf(ids[i])
        const sb = sizeOf(ids[j])
        const ox = (sa.width + sb.width) / 2 + GAP - Math.abs(a.x - b.x)
        const oy = (sa.height + sb.height) / 2 + GAP - Math.abs(a.y - b.y)
        if (ox <= 0 || oy <= 0) continue
        moved = true
        const fixA = ids[i] === focalId
        const fixB = ids[j] === focalId
        const share = fixA || fixB ? 1 : 0.5
        if (oy <= ox) {
          const d = (b.y >= a.y ? 1 : -1) * oy
          if (!fixA) a.y -= d * share
          if (!fixB) b.y += d * share
        } else {
          const d = (b.x >= a.x ? 1 : -1) * ox
          if (!fixA) a.x -= d * share
          if (!fixB) b.x += d * share
        }
      }
    }
    if (!moved) break
  }

  // Top-left positions, shifted into the positive quadrant.
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxYY = -Infinity
  for (const id of ids) {
    const c = centres.get(id)!
    const s = sizeOf(id)
    minX = Math.min(minX, c.x - s.width / 2)
    minY = Math.min(minY, c.y - s.height / 2)
    maxX = Math.max(maxX, c.x + s.width / 2)
    maxYY = Math.max(maxYY, c.y + s.height / 2)
  }
  const margin = 40
  const nodes = graph.nodes.map(n => {
    const c = centres.get(n.id)!
    const s = sizeOf(n.id)
    return { ...n, position: { x: c.x - s.width / 2 - minX + margin, y: c.y - s.height / 2 - minY + margin } }
  })
  void size
  return {
    nodes,
    edges: graph.edges.map(e => ({ ...e })),
    virtualWidth: maxX - minX + margin * 2,
    virtualHeight: maxYY - minY + margin * 2,
  }
}

/** Entities view layout: person-centred when possible, otherwise the hub-and-spoke force layout. */
export function layoutEntitiesAuto(graph: KGraph, size: { width: number; height: number }): LayoutForceResult & { focalId: string | null } {
  const focal = layoutEntitiesFocal(graph, size)
  if (focal) return { ...focal, focalId: focalEntityId(graph) }
  return { ...layoutEntities(graph, size), focalId: null }
}

// ── Edge label placement ─────────────────────────────────────────────────────

interface Box {
  cx: number
  cy: number
  hw: number
  hh: number
}

/** Point where the ray from the box centre towards (tx, ty) leaves the box (+ gap) — same as FloatingStraightEdge. */
export function borderPointOf(box: Box, tx: number, ty: number, gap = 3): { x: number; y: number } {
  const dx = tx - box.cx
  const dy = ty - box.cy
  if (dx === 0 && dy === 0) return { x: box.cx, y: box.cy }
  const sx = dx !== 0 ? (box.hw + gap) / Math.abs(dx) : Infinity
  const sy = dy !== 0 ? (box.hh + gap) / Math.abs(dy) : Infinity
  const s = Math.min(sx, sy, 1)
  return { x: box.cx + dx * s, y: box.cy + dy * s }
}

/** Rough pixel size of an edge label pill (10.5px text + padding; + score chip). */
export function edgeLabelSize(text: string, hasScore: boolean, extra = 0): { width: number; height: number } {
  return { width: Math.ceil(text.length * 6.3 + 22 + (hasScore ? 38 : 0) + extra), height: 22 }
}

const LABEL_TS = [0.5, 0.4, 0.6, 0.32, 0.68, 0.25, 0.75, 0.18, 0.82]

/**
 * Where along each labelled edge (fraction t of the border-to-border segment)
 * its label goes so labels don't sit on nodes or on each other. Primary
 * (verification) labels are placed first and get the best spots.
 */
export function placeEdgeLabels(
  nodes: { id: string; position: { x: number; y: number }; width: number; height: number }[],
  edges: { id: string; source: string; target: string; text: string; hasScore: boolean; primary: boolean; extra?: number }[],
): Map<string, number> {
  const boxes = new Map<string, Box>()
  for (const n of nodes) boxes.set(n.id, { cx: n.position.x + n.width / 2, cy: n.position.y + n.height / 2, hw: n.width / 2, hh: n.height / 2 })
  const placed: { x0: number; y0: number; x1: number; y1: number }[] = []
  const nodeRects = [...boxes.values()].map(b => ({ x0: b.cx - b.hw - 4, y0: b.cy - b.hh - 4, x1: b.cx + b.hw + 4, y1: b.cy + b.hh + 4 }))
  const overlap = (a: { x0: number; y0: number; x1: number; y1: number }, b: { x0: number; y0: number; x1: number; y1: number }) =>
    Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0)) * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0))
  const out = new Map<string, number>()
  const order = [...edges].sort((a, b) => Number(b.primary) - Number(a.primary))
  for (const e of order) {
    const a = boxes.get(e.source)
    const b = boxes.get(e.target)
    if (!a || !b) continue
    const s = borderPointOf(a, b.cx, b.cy)
    const t = borderPointOf(b, a.cx, a.cy, 5)
    const size = edgeLabelSize(e.text, e.hasScore, e.extra || 0)
    let best = 0.5
    let bestCost = Infinity
    for (const f of LABEL_TS) {
      const x = s.x + (t.x - s.x) * f
      const y = s.y + (t.y - s.y) * f
      const r = { x0: x - size.width / 2, y0: y - size.height / 2, x1: x + size.width / 2, y1: y + size.height / 2 }
      let cost = 0
      for (const nr of nodeRects) cost += overlap(r, nr) * 2
      for (const pr of placed) cost += overlap(r, pr) * 3
      cost += Math.abs(f - 0.5) * 40 // prefer the middle
      if (cost < bestCost) {
        bestCost = cost
        best = f
      }
    }
    out.set(e.id, best)
    const x = s.x + (t.x - s.x) * best
    const y = s.y + (t.y - s.y) * best
    // Reserve the spot for primary labels; hover-only labels don't block later ones.
    if (e.primary) placed.push({ x0: x - size.width / 2 - 3, y0: y - size.height / 2 - 3, x1: x + size.width / 2 + 3, y1: y + size.height / 2 + 3 })
  }
  return out
}
