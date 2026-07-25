import { useEffect, useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Sparkles, ChevronDown, ChevronRight, Package, User as UserIcon, Loader2, RefreshCw,
} from 'lucide-react'
import { Panel } from '@/components/ui/Panel'
import { Toggle } from '@/components/ui/Toggle'
import { clsx } from 'clsx'
import { listSkills, getSkill, setSkillEnabled, type Skill } from '@/lib/skills'

export function SkillsSection() {
  const [skills, setSkills] = useState<Skill[]>([])
  const [maxActive, setMaxActive] = useState(3)
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [bodies, setBodies] = useState<Record<string, string>>({})
  const [pendingBody, setPendingBody] = useState<string | null>(null)
  const [saving, setSaving] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const r = await listSkills()
      setSkills(r.skills)
      setMaxActive(r.max_active)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  async function toggle(name: string, next: boolean) {
    setSaving(name)
    // Optimistic update — the write is idempotent + local.
    setSkills(prev => prev.map(s => s.name === name ? { ...s, enabled: next } : s))
    try {
      const ok = await setSkillEnabled(name, next)
      if (!ok) await refresh()
    } finally {
      setSaving(null)
    }
  }

  async function expand(name: string) {
    if (expanded === name) { setExpanded(null); return }
    setExpanded(name)
    if (!bodies[name]) {
      setPendingBody(name)
      const sk = await getSkill(name)
      if (sk?.body) setBodies(prev => ({ ...prev, [name]: sk.body! }))
      setPendingBody(null)
    }
  }

  const enabledCount = skills.filter(s => s.enabled).length

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="font-serif text-2xl text-[var(--text-primary)] flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-[var(--accent)]" /> Skills
          </h3>
          <p className="text-sm text-[var(--text-muted)] mt-1 leading-relaxed">
            Reusable instruction bundles that get injected into the system prompt only
            when a small selector model decides they apply. Add your own by dropping a
            <code className="mx-1 px-1.5 py-0.5 rounded bg-[var(--accent-dim)] text-[var(--accent-hover)] font-mono text-[11px]">.md</code>
            file into <code className="mx-1 px-1.5 py-0.5 rounded bg-[var(--accent-dim)] text-[var(--accent-hover)] font-mono text-[11px]">~/.persephone/skills/</code>.
          </p>
        </div>
        <button
          onClick={refresh}
          disabled={loading}
          className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--accent)] px-2.5 py-1.5 rounded-md hover:bg-[var(--bg-tertiary)] transition-colors"
          title="Rescan skill folders"
        >
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          Rescan
        </button>
      </div>

      {/* Summary */}
      <Panel className="p-4">
        <div className="flex items-center justify-between text-sm">
          <div className="text-[var(--text-secondary)]">
            <span className="text-[var(--text-primary)] font-medium">{enabledCount}</span> of{' '}
            <span className="text-[var(--text-primary)] font-medium">{skills.length}</span> skill{skills.length === 1 ? '' : 's'} enabled
          </div>
          <div className="text-xs text-[var(--text-muted)]">
            Up to <span className="text-[var(--accent)] font-medium">{maxActive}</span> can fire per turn
          </div>
        </div>
      </Panel>

      {/* Skill list */}
      {loading && skills.length === 0 ? (
        <div className="flex items-center gap-2 text-sm text-[var(--text-muted)] py-6 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading skills…
        </div>
      ) : skills.length === 0 ? (
        <Panel className="p-6 text-center">
          <div className="text-sm text-[var(--text-muted)]">
            No skills found. Drop <code className="px-1.5 py-0.5 rounded bg-[var(--accent-dim)] text-[var(--accent-hover)] font-mono text-xs">.md</code> files into <code className="px-1.5 py-0.5 rounded bg-[var(--accent-dim)] text-[var(--accent-hover)] font-mono text-xs">~/.persephone/skills/</code> and click Rescan.
          </div>
        </Panel>
      ) : (
        <div className="space-y-2">
          {skills.map(skill => (
            <SkillCard
              key={skill.name}
              skill={skill}
              expanded={expanded === skill.name}
              body={bodies[skill.name]}
              bodyLoading={pendingBody === skill.name}
              saving={saving === skill.name}
              onToggle={next => toggle(skill.name, next)}
              onExpand={() => expand(skill.name)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function SkillCard({
  skill, expanded, body, bodyLoading, saving, onToggle, onExpand,
}: {
  skill: Skill
  expanded: boolean
  body?: string
  bodyLoading: boolean
  saving: boolean
  onToggle: (next: boolean) => void
  onExpand: () => void
}) {
  const SourceIcon = skill.source === 'user' ? UserIcon : Package

  return (
    <Panel className={clsx(
      'p-4 transition-colors',
      expanded && 'border-[var(--border-bright)]',
    )}>
      <div className="flex items-start gap-3">
        {/* Expand chevron */}
        <button
          onClick={onExpand}
          className="mt-0.5 p-1 rounded text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--bg-tertiary)] transition-colors"
          title={expanded ? 'Collapse' : 'Show body + keywords'}
        >
          {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>

        <div className="flex-1 min-w-0 cursor-pointer" onClick={onExpand}>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-[var(--text-primary)] text-sm">{skill.name}</span>
            <span className="flex items-center gap-1 text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] text-[var(--text-muted)] border border-[var(--border)]">
              <SourceIcon className="w-3 h-3" /> {skill.source}
            </span>
            {skill.category && skill.category !== 'general' && (
              <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-[var(--accent-dim)] text-[var(--accent)]">
                {skill.category}
              </span>
            )}
          </div>
          <p className="text-xs text-[var(--text-secondary)] mt-1 leading-relaxed line-clamp-2">
            {skill.description || <span className="italic text-[var(--text-muted)]">No description</span>}
          </p>
        </div>

        <div className="flex-shrink-0 pt-0.5">
          <Toggle
            checked={skill.enabled}
            onChange={onToggle}
            disabled={saving}
          />
        </div>
      </div>

      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="mt-4 pt-4 border-t border-[var(--border)] space-y-4">
              {skill.keywords.length > 0 && (
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1.5 font-medium">
                    Trigger keywords
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {skill.keywords.map(k => (
                      <span key={k} className="text-xs px-2 py-0.5 rounded-full bg-[var(--bg-tertiary)] text-[var(--text-secondary)] border border-[var(--border)]">
                        {k}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <div className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] mb-1.5 font-medium">
                  Instructions
                </div>
                {bodyLoading ? (
                  <div className="text-xs text-[var(--text-muted)] flex items-center gap-2">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…
                  </div>
                ) : (
                  <pre className="text-xs text-[var(--text-primary)] whitespace-pre-wrap font-sans leading-relaxed max-h-96 overflow-y-auto bg-[var(--bg-secondary)] rounded-lg p-3 border border-[var(--border)]">
                    {body || skill.body_preview || '(empty)'}
                  </pre>
                )}
              </div>
              <div className="text-[10px] text-[var(--text-muted)] font-mono truncate" title={skill.path}>
                {skill.path}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </Panel>
  )
}
