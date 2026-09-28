/**
 * "Story strip" for the Knowledge graph: a one-line, deterministic summary of
 * what the knowledge store knows about the scope's focal person, e.g.
 *   Michel Neuféglise → owns Neuféglise Digital Solutions (registry ✓)
 *     → signed Handwritten letter (96% · consistent, 5 references)
 *
 * Built only from stored facts (entities + relations), never from model text.
 * Pure (no React / DOM imports) so it can be bundled and checked in Node.
 */
import { friendlyDocName, friendlyDocNames, foldText } from './kgFormat'
import { SOCIAL_PLATFORMS, resolvePlatform } from './socialPlatforms'

interface StoryEntity {
  id: string
  type: string
  name: string
  props?: Record<string, unknown> | null
  mention_count?: number | null
}

interface StoryRelation {
  id: string
  src: string
  dst: string
  type: string
  confidence?: number | null
  props?: Record<string, unknown> | null
}

export type StoryTone = 'ok' | 'warn' | 'bad' | 'info' | 'neutral'

export interface StoryChip {
  key: string
  /** Relation verb shown muted before the object ("owns", "signed" …); empty for the person chip. */
  verb: string
  /** Main text (object name). */
  text: string
  /** Trailing qualifier ("96% · consistent, 5 references"). */
  note?: string
  /** Small check badge text ("registry"). */
  badge?: string
  tone: StoryTone
  kind: 'person' | 'organization' | 'document' | 'signature' | 'profile' | 'role'
  /** Entity to select when clicked. */
  nodeId?: string
  /** Relation to open (evidence) when clicked. */
  edgeId?: string
}

export interface Story {
  focalId: string
  focalName: string
  chips: StoryChip[]
}

/** Most-connected person entity (ties: mention count, then name); null when none has a relation. */
export function focalPerson(kg: { entities: StoryEntity[]; relations: StoryRelation[] } | null | undefined): StoryEntity | null {
  if (!kg || !Array.isArray(kg.entities)) return null
  const degree = new Map<string, number>()
  for (const r of kg.relations || []) {
    degree.set(r.src, (degree.get(r.src) || 0) + 1)
    degree.set(r.dst, (degree.get(r.dst) || 0) + 1)
  }
  let best: StoryEntity | null = null
  for (const e of kg.entities) {
    if (e.type !== 'person') continue
    const d = degree.get(e.id) || 0
    if (d === 0) continue
    if (
      !best ||
      d > (degree.get(best.id) || 0) ||
      (d === (degree.get(best.id) || 0) &&
        ((e.mention_count || 0) > (best.mention_count || 0) ||
          ((e.mention_count || 0) === (best.mention_count || 0) && e.name < best.name)))
    ) {
      best = e
    }
  }
  return best
}

const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)
const str = (v: unknown): string => (typeof v === 'string' ? v : '')

function bandTone(band: string): StoryTone {
  if (band === 'consistent') return 'ok'
  if (band === 'inconclusive') return 'warn'
  if (band === 'inconsistent') return 'bad'
  return 'neutral'
}

/** The story for a knowledge-graph snapshot, or null when there is nothing meaningful to say. */
export function buildStory(kg: { entities: StoryEntity[]; relations: StoryRelation[] } | null | undefined): Story | null {
  const focal = focalPerson(kg)
  if (!kg || !focal) return null
  const byId = new Map(kg.entities.map(e => [e.id, e]))
  const rels = (kg.relations || []).filter(r => byId.has(r.src) && byId.has(r.dst))
  const owner = [focal.name]
  const hintsOf = (e: StoryEntity) => ({ kind: str(e.props?.kind), signatureCard: !!e.props?.signature_card, ownerNames: owner })
  // Same names as the Entities view (colliding files get a distinguishing suffix).
  const docNames = friendlyDocNames(
    kg.entities.filter(e => e.type === 'document').map(e => ({ id: e.id, filename: str(e.props?.filename) || e.name, hints: hintsOf(e) })),
  )
  const docName = (e: StoryEntity) => docNames.get(e.id) ?? friendlyDocName(str(e.props?.filename) || e.name, hintsOf(e))

  const chips: StoryChip[] = [{ key: 'person', verb: '', text: focal.name, tone: 'neutral', kind: 'person', nodeId: focal.id }]

  // Ownership (registry-backed when the organisation comes from a registry extract)
  for (const r of rels.filter(r => r.type === 'owns' && r.src === focal.id).slice(0, 2)) {
    const org = byId.get(r.dst)!
    const op = org.props || {}
    const fromRegistry =
      !!(op.registry_number || op.legal_form || op.registered_since) ||
      rels.some(x => x.type === 'mentioned_in' && x.src === org.id && /registry extract/i.test(docName(byId.get(x.dst)!)))
    chips.push({
      key: `owns:${r.id}`,
      verb: 'owns',
      text: org.name,
      badge: fromRegistry ? 'registry' : undefined,
      tone: fromRegistry ? 'ok' : 'neutral',
      kind: 'organization',
      nodeId: org.id,
      edgeId: r.id,
    })
  }
  // Employment / role (only when there's no ownership story, to keep it one line)
  if (!chips.some(c => c.verb === 'owns')) {
    const w = rels.find(r => r.type === 'works_at' && r.src === focal.id)
    const role = rels.find(r => r.type === 'has_role' && r.src === focal.id)
    if (role) {
      chips.push({ key: `role:${role.id}`, verb: 'is', text: byId.get(role.dst)!.name, tone: 'neutral', kind: 'role', nodeId: role.dst, edgeId: role.id })
    }
    if (w) {
      chips.push({ key: `works:${w.id}`, verb: 'works at', text: byId.get(w.dst)!.name, tone: 'neutral', kind: 'organization', nodeId: w.dst, edgeId: w.id })
    }
  }

  // Signatures: signed letters, and letters whose signature did NOT match
  const signedLetters = new Set<string>()
  for (const r of rels.filter(r => r.type === 'signed' && r.src === focal.id).slice(0, 2)) {
    const doc = byId.get(r.dst)!
    signedLetters.add(doc.id)
    const p = r.props || {}
    const score = num(p.score) ?? (num(r.confidence) != null ? Math.round((r.confidence as number) * 100) : null)
    const band = str(p.band)
    const n = num(p.n_references)
    const note = [score != null ? `${score}%` : '', [band, n ? `${n} reference${n === 1 ? '' : 's'}` : ''].filter(Boolean).join(', ')]
      .filter(Boolean)
      .join(' · ')
    chips.push({ key: `signed:${r.id}`, verb: 'signed', text: docName(doc), note: note || undefined, tone: bandTone(band), kind: 'signature', nodeId: doc.id, edgeId: r.id })
  }
  const focalKey = foldText(focal.name).trim()
  for (const r of rels.filter(r => r.type === 'verified_against')) {
    const p = r.props || {}
    if (str(p.band) !== 'inconsistent' || signedLetters.has(r.src)) continue
    if (foldText(str(p.person)).trim() !== focalKey) continue
    const letter = byId.get(r.src)!
    const score = num(p.score)
    chips.push({
      key: `forged:${r.id}`,
      verb: 'signature',
      text: 'inconsistent',
      note: [score != null ? `${score}%` : '', docName(letter)].filter(Boolean).join(' · '),
      tone: 'bad',
      kind: 'signature',
      nodeId: letter.id,
      edgeId: r.id,
    })
    signedLetters.add(r.src)
  }

  // Online profiles: one "likely match" per platform
  const platforms = new Set<string>()
  for (const r of rels.filter(r => r.type === 'likely_profile' && r.src === focal.id)) {
    const prof = byId.get(r.dst)!
    const platform = resolvePlatform(prof.props?.platform, str(prof.props?.url))
    const label = platform ? SOCIAL_PLATFORMS[platform].label : 'Web'
    if (platforms.has(label)) continue
    platforms.add(label)
    chips.push({ key: `profile:${r.id}`, verb: `${label}:`, text: 'likely match', tone: 'info', kind: 'profile', nodeId: prof.id, edgeId: r.id })
  }

  if (chips.length <= 1) return null
  return { focalId: focal.id, focalName: focal.name, chips }
}
