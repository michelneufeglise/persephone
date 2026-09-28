/**
 * Knowledge graph: the evidence side panel (provenance of one relation) and
 * the story strip (one-line summary of the focal person's verified facts).
 */
import React, { useEffect, useMemo, useState } from 'react'
import { X, FileText, ShieldCheck, ShieldAlert, ShieldQuestion, BadgeCheck, User, Building2, Signature, Globe, Briefcase, Clock } from 'lucide-react'
import { clsx } from 'clsx'
import { fetchRelationEvidence, type KGRelationEvidence } from '@/lib/docAgent'
import { friendlyDocName } from './kgFormat'
import { relationLabel } from './kgEntities'
import type { Story, StoryChip } from './kgStory'
import { bandColor } from './kgEntityEdge'

const LABEL_CLS = 'text-[0.62rem] text-[var(--text-muted)] uppercase font-bold tracking-wider mb-1'
const VALUE_CLS = 'text-[0.78rem] text-[var(--text-secondary)] leading-snug'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className={LABEL_CLS}>{title}</div>
      {children}
    </div>
  )
}

function fmtTime(v: number | string | null | undefined): string {
  if (v == null || v === '') return ''
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(v)
  if (Number.isNaN(d.getTime())) return String(v)
  return d.toLocaleString(undefined, { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/** Snippet with the entity names highlighted. */
function Highlighted({ text, terms }: { text: string; terms: string[] }) {
  const parts = useMemo(() => {
    const clean = terms.filter(t => t && t.length >= 2).map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    if (clean.length === 0) return [text]
    return text.split(new RegExp(`(${clean.join('|')})`, 'gi'))
  }, [text, terms])
  const lower = terms.map(t => t.toLowerCase())
  return (
    <>
      {parts.map((p, i) =>
        lower.includes(p.toLowerCase()) ? (
          <mark key={i} className="kg-evidence-mark">
            {p}
          </mark>
        ) : (
          <React.Fragment key={i}>{p}</React.Fragment>
        ),
      )}
    </>
  )
}

function DocLine({ doc, ownerNames }: { doc: NonNullable<KGRelationEvidence['source_doc']>; ownerNames: string[] }) {
  const friendly = friendlyDocName(doc.filename, { kind: doc.kind, signatureCard: doc.signature_card, ownerNames })
  return (
    <div className="flex items-start gap-2 min-w-0">
      <FileText className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-[var(--text-muted)]" />
      <div className="min-w-0">
        <div className="text-[0.8rem] font-semibold text-[var(--text-primary)] leading-snug">{friendly}</div>
        <div className="text-[0.66rem] font-mono text-[var(--text-muted)] break-all" title={doc.filename}>
          {doc.filename}
        </div>
      </div>
    </div>
  )
}

export interface EvidenceRelationRef {
  id: string
  type: string
  label: string
}

/** Right-side panel: provenance of one knowledge-graph relation. */
export function KgEvidencePanel({
  relationId,
  others,
  onClose,
  onSelectRelation,
  onSelectEntity,
}: {
  relationId: string
  /** Other relations between the same two entities (aggregated under one edge). */
  others?: EvidenceRelationRef[]
  onClose: () => void
  onSelectRelation?: (relationId: string) => void
  onSelectEntity?: (entityId: string) => void
}) {
  const [ev, setEv] = useState<KGRelationEvidence | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const ac = new AbortController()
    setLoading(true)
    setError(null)
    fetchRelationEvidence(relationId, ac.signal)
      .then(d => setEv(d))
      .catch(e => {
        if (ac.signal.aborted) return
        setEv(null)
        setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false)
      })
    return () => ac.abort()
  }, [relationId])

  const rel = ev?.relation
  const ownerNames = ev ? [ev.src_entity, ev.dst_entity].filter(e => e.type === 'person').map(e => e.name) : []
  const nameOf = (e: { type: string; name: string } | undefined) =>
    !e ? '' : e.type === 'document' ? friendlyDocName(e.name, { ownerNames }) : e.name
  const sig = ev?.signature
  const sc = sig && sig.score != null ? bandColor(sig.band) : null
  const conf = rel && typeof rel.confidence === 'number' ? Math.round(rel.confidence * 100) : null
  const SigIcon = sig?.band === 'consistent' ? ShieldCheck : sig?.band === 'inconsistent' ? ShieldAlert : ShieldQuestion

  return (
    <aside className="absolute right-2 top-2 bottom-2 w-80 max-w-[calc(100%-1rem)] z-20 animate-in slide-in-from-right-4 duration-200" aria-label="Relation evidence">
      <div className="glass-strong kg-side-panel w-full h-full rounded-2xl flex flex-col overflow-hidden shadow-[var(--shadow-soft)]">
        <div className="flex items-start gap-2 px-3.5 pt-3 pb-2 border-b border-[var(--glass-stroke)]">
          <div className="min-w-0 flex-1">
            <div className="text-[0.6rem] font-bold uppercase tracking-[0.12em] text-[var(--accent)]">Evidence · relation</div>
            {rel && ev ? (
              <div className="text-[0.86rem] font-semibold text-[var(--text-primary)] leading-snug break-words">
                <button type="button" className="hover:underline text-left" onClick={() => onSelectEntity?.(ev.src_entity.id)}>
                  {nameOf(ev.src_entity)}
                </button>{' '}
                <span className="text-[var(--accent)]">{relationLabel(rel.type)}</span>{' '}
                <button type="button" className="hover:underline text-left" onClick={() => onSelectEntity?.(ev.dst_entity.id)}>
                  {nameOf(ev.dst_entity)}
                </button>
              </div>
            ) : (
              <div className="text-[0.86rem] font-semibold text-[var(--text-primary)]">{loading ? 'Loading…' : 'Relation'}</div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex-shrink-0 text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-[var(--glass-fill-hover)] rounded p-1 transition-colors"
            aria-label="Close evidence"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-3.5 py-3 space-y-3.5">
          {error && <div className="text-[0.74rem] text-red-500">{error}</div>}
          {loading && !ev && <div className="text-[0.74rem] text-[var(--text-muted)] animate-pulse">Loading evidence…</div>}
          {rel && ev && (
            <>
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="px-2 py-0.5 rounded-full bg-[var(--accent-dim)] text-[var(--accent)] font-semibold uppercase tracking-wider text-[0.6rem]">
                  {relationLabel(rel.type)}
                </span>
                {sig && sig.score != null && sc ? (
                  <span className="px-2 py-0.5 rounded-full text-[0.66rem] font-semibold" style={{ color: sc, background: `color-mix(in oklab, ${sc} 15%, transparent)` }}>
                    {sig.score}%{sig.band ? ` · ${sig.band}` : ''}
                  </span>
                ) : conf != null ? (
                  <span className="px-2 py-0.5 rounded-full bg-[var(--glass-fill-hover)] text-[0.66rem] font-mono text-[var(--text-secondary)]" title="Confidence">
                    confidence {conf}%
                  </span>
                ) : null}
                <span className="text-[0.64rem] text-[var(--text-muted)]">
                  via {rel.source === 'signature_engine' ? 'signature engine' : rel.source === 'web_lookup' ? 'web lookup' : 'document agent'}
                </span>
              </div>

              {sig && (
                <Section title="Signature check">
                  <div className="rounded-xl border border-[var(--glass-stroke)] px-2.5 py-2 space-y-2">
                    <div className="flex items-center gap-2">
                      <SigIcon className="w-4 h-4 flex-shrink-0" style={{ color: sc || 'var(--text-muted)' }} />
                      <div className={VALUE_CLS}>
                        {sig.score != null ? <b style={{ color: sc || undefined }}>{sig.score}%</b> : '—'}
                        {sig.band ? ` · ${sig.band}` : ''}
                        {sig.n_references ? ` · ${sig.n_references} reference signature${sig.n_references === 1 ? '' : 's'}` : ''}
                      </div>
                    </div>
                    {sig.questioned || sig.references.length > 0 ? (
                      <div className="grid grid-cols-3 gap-1">
                        {sig.questioned && (
                          <a href={sig.questioned} target="_blank" rel="noopener noreferrer" className="col-span-3 block rounded border border-[var(--glass-stroke)] bg-white overflow-hidden" title="Questioned signature (from the document)">
                            <img src={sig.questioned} alt="Questioned signature" className="w-full h-14 object-contain" />
                          </a>
                        )}
                        {sig.references.slice(0, 6).map((u, i) => (
                          <a key={u} href={u} target="_blank" rel="noopener noreferrer" className="block rounded border border-[var(--glass-stroke)] bg-white overflow-hidden" title={`Reference R${i + 1}`}>
                            <img src={u} alt={`Reference ${i + 1}`} className="w-full h-8 object-contain" />
                          </a>
                        ))}
                      </div>
                    ) : (
                      <div className="text-[0.68rem] text-[var(--text-muted)]">Signature crops are not available for this (older) check.</div>
                    )}
                    <div className="text-[0.62rem] text-[var(--text-muted)] leading-snug">
                      Questioned signature on top, reference specimens below. Automated comparison — not a forensic determination.
                    </div>
                  </div>
                </Section>
              )}

              {ev.source_doc && (
                <Section title={sig ? 'Questioned document' : 'Source document'}>
                  <DocLine doc={ev.source_doc} ownerNames={ownerNames} />
                </Section>
              )}
              {ev.reference_doc && (
                <Section title="Reference document">
                  <DocLine doc={ev.reference_doc} ownerNames={ownerNames} />
                </Section>
              )}

              {!sig && (
                <Section title="Evidence">
                  {ev.snippet ? (
                    <>
                      <blockquote className="kg-evidence-snippet">
                        <Highlighted text={ev.snippet} terms={ev.highlights || []} />
                      </blockquote>
                      <div className="text-[0.62rem] text-[var(--text-muted)] mt-1">
                        {ev.snippet_source === 'document' ? 'Excerpt from the document text' : 'Stored mention'}
                      </div>
                    </>
                  ) : (
                    <div className="text-[0.72rem] text-[var(--text-muted)]">No text excerpt stored for this relation.</div>
                  )}
                </Section>
              )}

              <Section title="Recorded">
                <div className={clsx(VALUE_CLS, 'space-y-0.5')}>
                  {ev.conversation && (
                    <div className="break-words" title={ev.conversation.id}>
                      {ev.conversation.title ? (ev.conversation.title.length >= 60 ? `${ev.conversation.title.trimEnd()}…` : ev.conversation.title) : 'Conversation'}
                    </div>
                  )}
                  <div className="flex items-center gap-1 text-[0.68rem] text-[var(--text-muted)] font-mono">
                    <Clock className="w-3 h-3" />
                    {fmtTime(rel.created_at) || '—'}
                    {rel.run_id ? ` · run ${rel.run_id.slice(0, 8)}` : ''}
                  </div>
                  {sig?.verified_at && <div className="text-[0.68rem] text-[var(--text-muted)]">signature verified {fmtTime(sig.verified_at)}</div>}
                </div>
              </Section>

              {others && others.length > 0 && (
                <Section title="Also between these two">
                  <div className="flex flex-wrap gap-1">
                    {others.map(o => (
                      <button
                        key={o.id}
                        type="button"
                        onClick={() => onSelectRelation?.(o.id)}
                        className="px-2 py-0.5 rounded-full border border-[var(--glass-stroke)] text-[0.68rem] text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-[var(--accent)] transition-colors"
                      >
                        {o.label}
                      </button>
                    ))}
                  </div>
                </Section>
              )}
            </>
          )}
        </div>
      </div>
    </aside>
  )
}

// ── Story strip ─────────────────────────────────────────────────────────────

const TONE_COLOR: Record<StoryChip['tone'], string> = {
  ok: 'rgb(16 185 129)',
  warn: 'rgb(245 158 11)',
  bad: 'rgb(239 68 68)',
  info: 'var(--holo)',
  neutral: 'var(--text-secondary)',
}

const KIND_ICON: Record<StoryChip['kind'], React.ElementType> = {
  person: User,
  organization: Building2,
  document: FileText,
  signature: Signature,
  profile: Globe,
  role: Briefcase,
}

/** One-line summary of the focal person's facts; chips select the node / relation. */
export function KgStoryStrip({ story, activeKey, onChip }: { story: Story; activeKey?: string | null; onChip: (chip: StoryChip) => void }) {
  return (
    <div className="kg-story" role="group" aria-label="Knowledge summary">
      {story.chips.map((c, i) => {
        const Icon = c.tone === 'bad' ? ShieldAlert : KIND_ICON[c.kind]
        const color = TONE_COLOR[c.tone]
        return (
          <span key={c.key} className="kg-story__step">
            {i > 0 && (
              <span className="kg-story__arrow" aria-hidden>
                →
              </span>
            )}
            <button
              type="button"
              onClick={() => onChip(c)}
              className={clsx('kg-story__chip', `is-${c.tone}`, activeKey === c.key && 'is-active', i === 0 && 'is-person')}
              style={{ ['--chip-tone' as string]: color }}
              title={c.edgeId ? 'Show the evidence for this fact' : 'Select in the entity graph'}
            >
              <Icon className="w-3.5 h-3.5 flex-shrink-0" style={{ color }} />
              {c.verb && <span className="kg-story__verb">{c.verb}</span>}
              <span className="kg-story__text">{c.text}</span>
              {c.badge && (
                <span className="kg-story__badge">
                  {c.badge}
                  <BadgeCheck className="w-3 h-3" />
                </span>
              )}
              {c.note && <span className="kg-story__note">({c.note})</span>}
            </button>
          </span>
        )
      })}
    </div>
  )
}
