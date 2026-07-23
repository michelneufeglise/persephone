import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { MessageCircle, Settings, Plus, Trash2, Pin, Brain, Microscope, Clapperboard, FileText, Music4, Bot } from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { PersephoneIcon } from '@/components/PersephoneIcon'
import type { Conversation } from '@/types'

export function Sidebar() {
  const {
    conversations,
    activeConversationId,
    openTab,
    deleteConversation,
    updateConversation,
    createNewConversation,
    currentView,
    setCurrentView,
  } = useAppStore()

  // Only show the Music tab if Ableton is detected on this machine.
  // Cheap probe on mount — /api/ableton/status is fast (< 100ms) and cache-friendly.
  const [abletonAvailable, setAbletonAvailable] = useState(false)
  useEffect(() => {
    let mounted = true
    fetch('/api/ableton/status')
      .then(r => r.json())
      .then(d => { if (mounted) setAbletonAvailable(!!d?.installed) })
      .catch(() => {})
    return () => { mounted = false }
  }, [])

  const sortedConvs = [...conversations].sort((a, b) => {
    if (a.pinned && !b.pinned) return -1
    if (!a.pinned && b.pinned) return 1
    return b.updatedAt - a.updatedAt
  })

  return (
    <div className="w-64 flex-shrink-0 flex flex-col h-full glass border-r border-[var(--border)]"
         style={{ borderRadius: 0 }}>
      {/* ── Logo + wordmark ─────────────────────────────────────────── */}
      {/* Extra top padding (pt-10 instead of py-5) so the logo sits below
          the macOS Electron traffic-light buttons. Marked `window-drag`
          so the user can grab this whole header to move the window —
          matches native macOS app behaviour where the title bar area is
          draggable. */}
      <div className="window-drag relative flex items-center gap-3 px-5 pt-10 pb-5 border-b border-[var(--border)]">
        <PersephoneIcon size={40} />
        <div className="flex flex-col leading-none">
          <span className="font-display text-xl tracking-tight text-[var(--text-primary)]">
            Persephone
          </span>
          <span className="font-mono text-[9px] uppercase tracking-[0.28em] text-[var(--text-muted)] mt-1">
            queen between worlds
          </span>
        </div>
      </div>

      {/* ── Nav ─────────────────────────────────────────────────────── */}
      <div className="px-3 py-3 space-y-1 border-b border-[var(--border)]">
        <NavItem
          icon={MessageCircle}
          label="Chat"
          active={currentView === 'chat'}
          onClick={() => setCurrentView('chat')}
        />
        <NavItem
          icon={Clapperboard}
          label="Reels"
          active={currentView === 'reels'}
          onClick={() => setCurrentView('reels')}
        />
        <NavItem
          icon={FileText}
          label="Documents"
          active={currentView === 'documents'}
          onClick={() => setCurrentView('documents')}
        />
        {abletonAvailable && (
          <NavItem
            icon={Music4}
            label="Music"
            active={currentView === 'music'}
            onClick={() => setCurrentView('music')}
          />
        )}
        <NavItem
          icon={Microscope}
          label="Research"
          active={currentView === 'research'}
          onClick={() => setCurrentView('research')}
        />
        <NavItem
          icon={Brain}
          label="Memory"
          active={currentView === 'memory'}
          onClick={() => setCurrentView('memory')}
        />
        <NavItem
          icon={Bot}
          label="Workers"
          active={currentView === 'workers'}
          onClick={() => setCurrentView('workers')}
        />
        <NavItem
          icon={Settings}
          label="Settings"
          active={currentView === 'settings'}
          onClick={() => setCurrentView('settings')}
        />
      </div>

      {/* ── Conversations ───────────────────────────────────────────── */}
      {currentView === 'chat' && (
        <>
          <div className="flex items-center justify-between px-5 py-3">
            <span className="font-mono text-[10px] uppercase tracking-[0.28em] text-[var(--text-muted)]">
              History
            </span>
            <button
              onClick={createNewConversation}
              className="p-1.5 rounded-md text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)] transition-colors"
              title="New conversation"
            >
              <Plus className="w-3.5 h-3.5" />
            </button>
          </div>

          <div
            className="flex-1 overflow-y-auto px-2 space-y-1 pb-4"
            style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--scrollbar) transparent' }}
          >
            <AnimatePresence>
              {sortedConvs.map(conv => (
                <ConvItem
                  key={conv.id}
                  conv={conv}
                  isActive={conv.id === activeConversationId}
                  onSelect={() => {
                    // openTab activates + folds into the tab strip so
                    // sidebar picks feel like browser bookmarks.
                    openTab(conv.id)
                    setCurrentView('chat')
                  }}
                  onDelete={() => deleteConversation(conv.id)}
                  onTogglePin={() => updateConversation(conv.id, { pinned: !conv.pinned })}
                />
              ))}
            </AnimatePresence>

            {sortedConvs.length === 0 && (
              <p className="text-xs text-[var(--text-muted)] px-4 py-6 text-center font-display-italic">
                No conversations yet — speak, and she will answer.
              </p>
            )}
          </div>
        </>
      )}

    </div>
  )
}

/* ─── Nav row ──────────────────────────────────────────────────────── */
function NavItem({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: React.ElementType
  label: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`group relative flex items-center gap-3 w-full px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-300 text-left overflow-hidden
        ${active
          ? 'text-[var(--text-primary)] bg-[var(--bg-tertiary)] shadow-[var(--shadow-soft)]'
          : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]/40 hover:text-[var(--text-primary)]'
        }`}
    >
      {/* active vertical rail */}
      {active && (
        <motion.span
          layoutId="nav-rail"
          className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-full"
          style={{
            background:
              'linear-gradient(180deg, var(--accent), var(--holo))',
            boxShadow: '0 0 12px var(--accent-glow)',
          }}
        />
      )}
      <Icon className={`w-4 h-4 flex-shrink-0 transition-colors ${active ? 'text-[var(--accent)]' : ''}`} />
      <span className="tracking-tight">{label}</span>
    </button>
  )
}

/* ─── Conversation row ─────────────────────────────────────────────── */
function ConvItem({
  conv,
  isActive,
  onSelect,
  onDelete,
  onTogglePin,
}: {
  conv: Conversation
  isActive: boolean
  onSelect: () => void
  onDelete: () => void
  onTogglePin: () => void
}) {
  return (
    <motion.div
      initial={{ opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -8 }}
      transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
      className={`group relative flex items-center gap-2 px-3 py-2.5 rounded-xl cursor-pointer transition-all duration-200
        ${isActive
          ? 'bg-[var(--bg-tertiary)] shadow-[var(--shadow-soft)]'
          : 'hover:bg-[var(--bg-tertiary)]/50'
        }`}
      onClick={onSelect}
    >
      {isActive && (
        <span
          className="absolute left-0 top-2 bottom-2 w-[2px] rounded-full"
          style={{ background: 'linear-gradient(180deg, var(--accent), var(--holo))', boxShadow: '0 0 8px var(--accent-glow)' }}
        />
      )}
      <div className="flex-1 min-w-0 pl-1">
        <div className={`text-xs font-medium truncate leading-snug ${isActive ? 'text-[var(--text-primary)]' : 'text-[var(--text-secondary)]'}`}>
          {conv.title}
        </div>
        <div className="text-[10px] text-[var(--text-muted)] mt-0.5 font-mono tracking-wider">
          {conv.messages.length} msg · {new Date(conv.updatedAt).toLocaleDateString()}
        </div>
      </div>

      <div className="flex-shrink-0 hidden group-hover:flex items-center gap-0.5">
        <button
          onClick={e => { e.stopPropagation(); onTogglePin() }}
          className={`p-1 rounded-md transition-colors ${conv.pinned ? 'text-[var(--gold)]' : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]'}`}
        >
          <Pin className="w-3 h-3" />
        </button>
        <button
          onClick={e => { e.stopPropagation(); onDelete() }}
          className="p-1 rounded-md text-[var(--text-muted)] hover:text-red-400 transition-colors"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      </div>
    </motion.div>
  )
}
