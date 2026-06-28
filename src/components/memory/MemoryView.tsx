import { useEffect, useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Brain, MessageSquare, Trash2, Sparkles, RefreshCw, Tag,
  Hash, Pin, Plus, X,
} from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { Panel } from '@/components/ui/Panel'
import { clsx } from 'clsx'

interface UserFact {
  id:         number
  fact:       string
  category:   string
  confidence: number
  sourceConv: string | null
  sourceMsg:  string | null
  createdAt:  number
}

type Tab = 'facts' | 'conversations'

export function MemoryView() {
  const { conversations, deleteConversation, setActiveConversation, setCurrentView } = useAppStore()
  const [tab, setTab] = useState<Tab>('facts')
  const [facts, setFacts] = useState<UserFact[]>([])
  const [loading, setLoading] = useState(false)
  const [adding, setAdding] = useState(false)
  const [newFact, setNewFact] = useState('')
  const [newCat, setNewCat]   = useState('general')

  const loadFacts = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch('/api/memory/facts')
      const d = await r.json()
      setFacts(d.facts ?? [])
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { loadFacts() }, [loadFacts])
  // Poll while on the page so newly-extracted facts show up live
  useEffect(() => {
    if (tab !== 'facts') return
    const t = setInterval(loadFacts, 8000)
    return () => clearInterval(t)
  }, [tab, loadFacts])

  async function deleteFact(id: number) {
    setFacts(fs => fs.filter(f => f.id !== id))
    await fetch(`/api/memory/facts/${id}`, { method: 'DELETE' })
  }

  async function addFact() {
    const fact = newFact.trim()
    if (!fact) return
    await fetch('/api/memory/facts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fact, category: newCat || 'general' }),
    })
    setNewFact(''); setNewCat('general'); setAdding(false)
    await loadFacts()
  }

  async function clearAll() {
    if (!confirm('Delete ALL stored facts about you? This cannot be undone.')) return
    await fetch('/api/memory/facts', { method: 'DELETE' })
    await loadFacts()
  }

  const totalMessages = conversations.reduce((s, c) => s + c.messages.length, 0)

  // group facts by category for nicer display
  const grouped: Record<string, UserFact[]> = {}
  for (const f of facts) (grouped[f.category] ??= []).push(f)
  const orderedCats = ['name', 'work', 'location', 'preferences', 'family', 'projects', 'hardware']
  const cats = [
    ...orderedCats.filter(c => grouped[c]),
    ...Object.keys(grouped).filter(c => !orderedCats.includes(c)).sort(),
  ]

  return (
    <div className="h-full glass rounded-3xl overflow-hidden flex flex-col">
      {/* Header */}
      <div className="px-6 py-4 border-b border-[var(--border)] bg-[var(--bg-glass-strong)]">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="relative w-10 h-10">
              <div className="absolute inset-0 rounded-full blur-md opacity-60"
                style={{ background: 'conic-gradient(from 220deg, var(--orb-color-1), var(--orb-color-3), var(--orb-color-2), var(--orb-color-1))' }} />
              <div className="relative w-10 h-10 rounded-full flex items-center justify-center"
                style={{
                  background: 'radial-gradient(circle at 30% 25%, rgba(255,255,255,0.4), transparent 35%), conic-gradient(from 220deg, var(--orb-color-1), var(--orb-color-3), var(--orb-color-2), var(--orb-color-1))',
                  boxShadow: 'inset 0 -3px 6px rgba(0,0,0,0.4), inset 0 2px 3px rgba(255,255,255,0.3), 0 0 14px var(--accent-glow)',
                }}>
                <Brain className="w-5 h-5 text-white" />
              </div>
            </div>
            <div>
              <h2 className="font-display text-2xl text-[var(--text-primary)] leading-none">Memory</h2>
              <p className="text-xs text-[var(--text-muted)] font-mono mt-1 tracking-wider">
                {facts.length} facts · {conversations.length} conversations · {totalMessages} messages
              </p>
            </div>
          </div>

          {/* tabs */}
          <div className="flex bg-[var(--bg-tertiary)] rounded-xl p-1 gap-1">
            <TabBtn active={tab === 'facts'} onClick={() => setTab('facts')} icon={Sparkles} label="Facts" />
            <TabBtn active={tab === 'conversations'} onClick={() => setTab('conversations')} icon={MessageSquare} label="History" />
          </div>
        </div>
      </div>

      {/* Body */}
      <div
        className="flex-1 overflow-y-auto p-6"
        style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--scrollbar) transparent' }}
      >
        <AnimatePresence mode="wait">
          {tab === 'facts' ? (
            <motion.div
              key="facts"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.2 }}
              className="space-y-4 max-w-3xl mx-auto"
            >
              {/* toolbar */}
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                  Persephone learns durable facts about you from every conversation and remembers them across <em>every model</em>. Edit or remove anything below.
                </p>
                <div className="flex items-center gap-1 flex-shrink-0">
                  <IconBtn onClick={loadFacts} title="Refresh" loading={loading}><RefreshCw className={clsx('w-3.5 h-3.5', loading && 'animate-spin')} /></IconBtn>
                  <IconBtn onClick={() => setAdding(a => !a)} title="Add fact"><Plus className="w-3.5 h-3.5" /></IconBtn>
                  {facts.length > 0 && (
                    <IconBtn onClick={clearAll} title="Clear all"><Trash2 className="w-3.5 h-3.5" /></IconBtn>
                  )}
                </div>
              </div>

              {/* add form */}
              <AnimatePresence>
                {adding && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="overflow-hidden"
                  >
                    <Panel className="p-3 space-y-2">
                      <input
                        autoFocus
                        value={newFact}
                        onChange={e => setNewFact(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') addFact() }}
                        placeholder='e.g. "The user prefers concise replies"'
                        className="w-full bg-[var(--bg-primary)] border border-[var(--border)] rounded-lg px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]"
                      />
                      <div className="flex items-center gap-2">
                        <select
                          value={newCat}
                          onChange={e => setNewCat(e.target.value)}
                          className="bg-[var(--bg-primary)] border border-[var(--border)] rounded-lg px-2 py-1.5 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]"
                        >
                          {orderedCats.concat(['other', 'general']).map(c => (
                            <option key={c} value={c}>{c}</option>
                          ))}
                        </select>
                        <button
                          onClick={addFact}
                          className="px-3 py-1.5 rounded-lg text-xs font-medium text-white"
                          style={{
                            background: 'linear-gradient(135deg, var(--accent), var(--accent-deep))',
                            boxShadow: '0 4px 12px -4px var(--accent-glow)',
                          }}
                        >
                          Save fact
                        </button>
                        <button onClick={() => setAdding(false)} className="text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)]">cancel</button>
                      </div>
                    </Panel>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* empty */}
              {!loading && facts.length === 0 && (
                <Panel className="p-12 text-center">
                  <Sparkles className="w-10 h-10 text-[var(--text-muted)] mx-auto mb-4" />
                  <p className="text-[var(--text-secondary)] font-display-italic">Nothing remembered yet.</p>
                  <p className="text-xs text-[var(--text-muted)] mt-2 max-w-xs mx-auto leading-relaxed">
                    Mention your name, work, or preferences — Persephone will quietly remember and apply it across every conversation and every model.
                  </p>
                </Panel>
              )}

              {/* grouped facts */}
              {cats.map(cat => (
                <div key={cat} className="space-y-2">
                  <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-[0.3em] text-[var(--text-muted)]">
                    <Tag className="w-3 h-3" />
                    {cat}
                    <span className="text-[var(--accent)]">·</span>
                    <span>{grouped[cat].length}</span>
                  </div>
                  <div className="space-y-1.5">
                    {grouped[cat].map(f => (
                      <FactRow key={f.id} fact={f} onDelete={() => deleteFact(f.id)} />
                    ))}
                  </div>
                </div>
              ))}
            </motion.div>
          ) : (
            <motion.div
              key="conv"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.2 }}
              className="space-y-3 max-w-3xl mx-auto"
            >
              {conversations.length === 0 && (
                <Panel className="p-12 text-center">
                  <MessageSquare className="w-10 h-10 text-[var(--text-muted)] mx-auto mb-4" />
                  <p className="text-[var(--text-secondary)] font-display-italic">No conversations yet.</p>
                </Panel>
              )}
              {conversations.map(conv => (
                <Panel
                  key={conv.id}
                  className="p-4 flex items-center gap-4 hover:border-[var(--border-bright)] transition-colors cursor-pointer group"
                  onClick={() => { setActiveConversation(conv.id); setCurrentView('chat') }}
                >
                  <div className="w-10 h-10 rounded-xl bg-[var(--accent-dim)] flex items-center justify-center flex-shrink-0">
                    {conv.pinned ? <Pin className="w-4 h-4 text-[var(--gold)]" /> : <MessageSquare className="w-5 h-5 text-[var(--accent)]" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-[var(--text-primary)] truncate">{conv.title}</div>
                    <div className="text-xs text-[var(--text-muted)] mt-0.5 font-mono">
                      {conv.messages.length} msg · {conv.model || 'unknown model'} · {new Date(conv.updatedAt).toLocaleString()}
                    </div>
                    {conv.messages.length > 0 && (
                      <div className="text-xs text-[var(--text-secondary)] mt-1.5 truncate italic">
                        "{conv.messages[conv.messages.length - 1]?.content?.slice(0, 90)}…"
                      </div>
                    )}
                  </div>
                  <button
                    onClick={e => { e.stopPropagation(); deleteConversation(conv.id) }}
                    className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-red-400 hover:bg-red-400/10 transition-colors opacity-0 group-hover:opacity-100"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </Panel>
              ))}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}

/* ─── pieces ───────────────────────────────────────────────────────── */
function FactRow({ fact, onDelete }: { fact: UserFact; onDelete: () => void }) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -4 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 4 }}
      className="group relative flex items-start gap-3 px-3.5 py-2.5 rounded-xl bg-[var(--bg-tertiary)] border border-[var(--border)] hover:border-[var(--border-bright)] transition-colors"
    >
      <Hash className="w-3 h-3 text-[var(--accent)] mt-1 flex-shrink-0 opacity-60" />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-[var(--text-primary)] leading-relaxed">{fact.fact}</p>
        <div className="text-[10px] font-mono text-[var(--text-muted)] mt-1 tracking-wider">
          {new Date(fact.createdAt).toLocaleString()}
          {fact.confidence < 1 && <span className="ml-2">conf {(fact.confidence * 100).toFixed(0)}%</span>}
        </div>
      </div>
      <button
        onClick={onDelete}
        className="opacity-0 group-hover:opacity-100 p-1 rounded text-[var(--text-muted)] hover:text-red-400 transition-all"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </motion.div>
  )
}

function TabBtn({
  active, onClick, icon: Icon, label,
}: { active: boolean; onClick: () => void; icon: React.ElementType; label: string }) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-200',
        active
          ? 'text-[var(--text-primary)] bg-[var(--bg-primary)] shadow-[var(--shadow-soft)]'
          : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
      )}
    >
      <Icon className={clsx('w-3.5 h-3.5', active && 'text-[var(--accent)]')} />
      {label}
    </button>
  )
}

function IconBtn({
  children, onClick, title, loading,
}: { children: React.ReactNode; onClick: () => void; title: string; loading?: boolean }) {
  return (
    <button
      onClick={onClick}
      title={title}
      disabled={loading}
      className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)] transition-colors disabled:opacity-50"
    >
      {children}
    </button>
  )
}
