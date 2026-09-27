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

  const nodes: KNode[] = kg.entities.map(entity => {
    const props = (entity.props || {}) as Record<string, unknown>
    const isProfile = entity.type === 'profile'
    const rels: EntityRelationView[] = []
    for (const r of relations) {
      if (r.src === entity.id) {
        rels.push({ id: r.id, type: r.type, label: relationLabel(r.type), direction: 'out', otherId: r.dst, otherName: byId.get(r.dst)!.name })
      } else if (r.dst === entity.id) {
        rels.push({ id: r.id, type: r.type, label: relationLabel(r.type), direction: 'in', otherId: r.src, otherName: byId.get(r.src)!.name })
      }
    }
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
        displayName: isProfile ? cleanProfileTitle(entity.name) : entity.name,
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

  const edges: KEdge[] = relations.map(r => ({
    id: r.id,
    source: r.src,
    target: r.dst,
    label: relationLabel(r.type),
    kind: r.type,
  }))

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
