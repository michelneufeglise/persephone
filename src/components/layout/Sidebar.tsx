import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { MessageCircle, Settings, Plus, Trash2, Pin, Brain, Microscope, Clapperboard, FileText, Music4, Bot, CalendarClock, Workflow } from 'lucide-react'
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
    <div className="w-64 flex-shrink-0 flex flex-col h-full glass rounded-3xl overflow-hidden">
      {/* ── Logo + wordmark ─────────────────────────────────────────── */}
      {/* Extra top padding (pt-10 instead of py-5) so the logo sits below
          the macOS Electron traffic-light buttons. Marked `window-drag`
          so the user can grab this whole header to move the window —
          matches native macOS app behaviour where the title bar area is
          draggable. */}
      <div className="window-drag relative flex items-center gap-3 px-5 pt-10 pb-5">
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
      <nav className="px-3 pb-3 space-y-4">
        <NavGroup label="Workspace">
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
        </NavGroup>

        <NavGroup label="Intelligence">
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
            icon={Workflow}
            label="Flows"
            active={currentView === 'flows'}
            onClick={() => setCurrentView('flows')}
          />
        </NavGroup>

        <NavGroup label="Automation">
          <NavItem
            icon={Bot}
            label="Workers"
            active={currentView === 'workers'}
            onClick={() => setCurrentView('workers')}
          />
          <NavItem
            icon={CalendarClock}
            label="Tasks"
            active={currentView === 'tasks'}
            onClick={() => setCurrentView('tasks')}
          />
        </NavGroup>

        <NavGroup label="System">
          <NavItem
            icon={Settings}
            label="Settings"
            active={currentView === 'settings'}
            onClick={() => setCurrentView('settings')}
          />
        </NavGroup>
      </nav>

      {/* ── Conversations ───────────────────────────────────────────── */}
      {currentView === 'chat' && (
        <>
          <div className="mx-4 border-t border-[var(--glass-stroke)]" />
          <div className="flex items-center justify-between px-5 py-3">
            <span className="section-label">
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

/* ─── Nav group ───────────────────────────────────────────────────── */
function NavGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="section-label px-3 mb-1.5">{label}</div>
      <div className="space-y-0.5">{children}</div>
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
      className={`group relative flex items-center gap-3 w-full px-3 py-2 rounded-xl text-sm font-medium transition-colors duration-200 text-left
        ${active
          ? 'text-[var(--text-primary)]'
          : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--glass-fill)]'
        }`}
    >
      {/* active pill */}
      {active && (
        <motion.span
          layoutId="nav-pill"
          className="absolute inset-0 rounded-xl"
          style={{
            background: 'var(--glass-fill-hover)',
            border: '1px solid var(--glass-stroke)',
            boxShadow: '0 1px 0 var(--glass-highlight) inset',
          }}
          transition={{ type: 'spring', stiffness: 420, damping: 34 }}
        />
      )}
      <Icon className={`w-4 h-4 flex-shrink-0 transition-colors relative ${active ? 'text-[var(--accent)]' : ''}`} />
      <span className="tracking-tight relative">{label}</span>
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
          ? 'glass-card glass-card-active'
          : 'border border-transparent hover:bg-[var(--glass-fill)]'
        }`}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0">
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
