import { forwardRef, useEffect, useMemo, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Plus, X, Loader2 } from 'lucide-react'
import { useAppStore } from '@/store/appStore'

/**
 * Browser-style tab strip that lives above the chat scroll area.
 *
 *   [ Chat 1 ×  ] [ Chat 2 ×  ] [ Chat 3 ×  ]  ...  [ + ]
 *
 * A "tab" is an entry in `openTabIds` from the app store. It always maps
 * 1:1 to a conversation in `conversations`. Closing a tab does NOT delete
 * the conversation — it just removes it from the strip; the user can
 * reopen it from the History list in the sidebar.
 *
 * Drag-and-drop reorder isn't wired here (Framer Motion Reorder would fit
 * nicely later); tabs currently stay in the order they were opened.
 */
export function ChatTabs() {
  const {
    conversations, activeConversationId, openTabIds,
    openTab, closeTab, createNewConversation,
  } = useAppStore()

  // Auto-heal: if the active conversation isn't in the tab strip yet,
  // fold it in. Happens on first mount for legacy state, and whenever
  // the user picks a conversation from the sidebar (History list).
  useEffect(() => {
    if (activeConversationId && !openTabIds.includes(activeConversationId)) {
      openTab(activeConversationId)
    }
  }, [activeConversationId, openTabIds, openTab])

  // Auto-heal: prune tab ids that no longer correspond to a real
  // conversation (e.g. user deleted from sidebar).
  useEffect(() => {
    const known = new Set(conversations.map(c => c.id))
    const stale = openTabIds.filter(id => !known.has(id))
    stale.forEach(id => closeTab(id))
  }, [conversations, openTabIds, closeTab])

  // Browser-style keyboard shortcuts.
  //   ⌘/Ctrl+T          → new tab
  //   ⌘/Ctrl+W          → close current tab
  //   ⌘/Ctrl+1..9       → switch to Nth tab (9 = last)
  //   ⌘/Ctrl+Shift+[ /] → prev / next tab
  //
  // Ignored when the user is typing in an input/textarea so we don't
  // steal ⌘+W from a form.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const mod = e.metaKey || e.ctrlKey
      if (!mod) return
      const target = e.target as HTMLElement | null
      const inField = target && (
        target.tagName === 'TEXTAREA' || target.tagName === 'INPUT'
        || target.isContentEditable
      )
      // ⌘+T — new tab (allowed even in fields; browsers do the same)
      if (e.key === 't' || e.key === 'T') {
        e.preventDefault()
        createNewConversation()
        return
      }
      if (inField) return
      if (e.key === 'w' || e.key === 'W') {
        if (activeConversationId) {
          e.preventDefault()
          closeTab(activeConversationId)
        }
        return
      }
      if (e.shiftKey && (e.key === ']' || e.key === '}')) {
        e.preventDefault()
        const idx = openTabIds.indexOf(activeConversationId ?? '')
        const next = openTabIds[(idx + 1) % openTabIds.length]
        if (next) openTab(next)
        return
      }
      if (e.shiftKey && (e.key === '[' || e.key === '{')) {
        e.preventDefault()
        const idx = openTabIds.indexOf(activeConversationId ?? '')
        const next = openTabIds[(idx - 1 + openTabIds.length) % openTabIds.length]
        if (next) openTab(next)
        return
      }
      // ⌘+1..9 — direct tab switch. 9 selects the LAST tab regardless of count.
      if (/^[1-9]$/.test(e.key)) {
        const n = parseInt(e.key, 10)
        const target = n === 9
          ? openTabIds[openTabIds.length - 1]
          : openTabIds[n - 1]
        if (target) {
          e.preventDefault()
          openTab(target)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [activeConversationId, openTabIds, openTab, closeTab, createNewConversation])

  const tabs = useMemo(
    () => openTabIds
      .map(id => conversations.find(c => c.id === id))
      .filter((c): c is NonNullable<typeof c> => !!c),
    [conversations, openTabIds],
  )

  const stripRef  = useRef<HTMLDivElement>(null)
  const activeRef = useRef<HTMLButtonElement>(null)

  // Keep the active tab visible when it changes off-screen.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
  }, [activeConversationId])

  if (tabs.length === 0) {
    return (
      <div className="flex items-center px-3 py-2 border-b border-[var(--border)] bg-[var(--bg-glass-strong)]">
        <button
          onClick={() => createNewConversation()}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium text-[var(--accent)] hover:bg-[var(--accent-dim)]/40 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" /> New tab
        </button>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-0.5 px-2 pt-2 border-b border-[var(--border)] bg-[var(--bg-glass-strong)] overflow-hidden">
      {/* Scrollable tab row — hides overflow so a long list doesn't wrap. */}
      <div
        ref={stripRef}
        className="flex-1 flex items-end gap-0.5 overflow-x-auto scrollbar-thin pb-0"
        style={{ scrollbarWidth: 'thin' }}
      >
        <AnimatePresence initial={false}>
          {tabs.map(conv => (
            <TabButton
              key={conv.id}
              ref={activeConversationId === conv.id ? activeRef : undefined}
              conv={conv}
              active={activeConversationId === conv.id}
              onActivate={() => openTab(conv.id)}
              onClose={() => closeTab(conv.id)}
              closable={tabs.length > 1}
            />
          ))}
        </AnimatePresence>
      </div>

      {/* + button — always visible at the right edge */}
      <button
        onClick={() => createNewConversation()}
        title="New tab (⌘/Ctrl+T)"
        className="flex-shrink-0 w-8 h-8 mb-0.5 rounded-md flex items-center justify-center text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)]/40 transition-colors"
      >
        <Plus className="w-4 h-4" />
      </button>
    </div>
  )
}


// ── One tab pill ─────────────────────────────────────────────────────────
interface TabButtonProps {
  conv:       ReturnType<typeof useAppStore.getState>['conversations'][number]
  active:     boolean
  onActivate: () => void
  onClose:    () => void
  closable:   boolean
}

// forwardRef is required in React 18 to accept a `ref` prop on a function
// component. React 19 allows ref as a plain prop, but the project's React
// is still 18, so we use the classic wrapper.
const TabButton = forwardRef<HTMLButtonElement, TabButtonProps>(function TabButton(
  { conv, active, onActivate, onClose, closable }, ref,
) {
  const [hover, setHover] = useState(false)
  // Whether this tab is showing a streaming response. Used to pulse a
  // small spinner in the tab so the user knows a background tab is
  // still generating, even when they've switched away.
  const isStreaming = conv.messages.length > 0 &&
    conv.messages[conv.messages.length - 1].isStreaming === true

  // Truncated title: prefer conv.title if user has set one, otherwise
  // pull the first user turn's opening words.
  const label = (() => {
    if (conv.title && conv.title !== 'New conversation') return conv.title
    const firstUser = conv.messages.find(m => m.role === 'user')
    if (firstUser?.content) {
      const snippet = firstUser.content.trim().split('\n')[0].slice(0, 32)
      return snippet || 'New conversation'
    }
    return 'New conversation'
  })()

  return (
    <motion.button
      ref={ref}
      onClick={onActivate}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 4 }}
      transition={{ duration: 0.15 }}
      className={`group relative flex-shrink-0 flex items-center gap-1.5 min-w-[120px] max-w-[220px]
        px-3 py-1.5 rounded-t-lg text-xs font-medium transition-colors border-t border-l border-r ${
        active
          ? 'text-[var(--text-primary)] bg-[var(--bg-primary)] border-[var(--border)] shadow-[0_-1px_0_var(--accent)]'
          : 'text-[var(--text-muted)] bg-[var(--bg-secondary)]/60 border-transparent hover:text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)]'
      }`}
      title={label}
    >
      {isStreaming
        ? <Loader2 className="w-3 h-3 flex-shrink-0 animate-spin text-[var(--accent)]" />
        : <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${active ? 'bg-[var(--accent)]' : 'bg-[var(--text-muted)]/40'}`} />}
      <span className="truncate flex-1 text-left">{label}</span>
      {closable && (
        <span
          onClick={(e) => { e.stopPropagation(); onClose() }}
          role="button"
          aria-label="Close tab"
          className={`flex-shrink-0 w-4 h-4 rounded-sm flex items-center justify-center transition-opacity ${
            hover || active ? 'opacity-100' : 'opacity-0'
          } hover:bg-[var(--bg-tertiary)] hover:text-red-300 text-[var(--text-muted)]`}
        >
          <X className="w-2.5 h-2.5" />
        </span>
      )}
    </motion.button>
  )
})
