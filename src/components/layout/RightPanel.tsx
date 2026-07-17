import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { Volume2, ChevronRight, ChevronLeft, Bot } from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { VoicePanel } from '@/components/voice/VoicePanel'
import { DelegatePanel } from '@/components/delegate/DelegatePanel'

/**
 * Right-hand panel for the chat view.
 *
 * Has two tabs: Voice (TTS controls) and Auxiliary Models (live view of the
 * background worker models the user dispatched via the "send to worker"
 * button). Auxiliary tab pulses a badge when in-flight tasks exist so the
 * user notices without switching.
 */
export function RightPanel() {
  const { voicePanelOpen, setVoicePanelOpen, rightPanel, setRightPanel, activeConversationId } = useAppStore()
  // Legacy: `rightPanel` used to include 'documents' which moved to its own
  // sidebar tab. Coerce that back to 'voice' so persisted settings don't
  // land users on a phantom tab.
  const active = rightPanel === 'documents' ? 'voice' : rightPanel
  const [inflightCount, setInflightCount] = useState(0)

  // Cheap poll to keep the "N in flight" badge on the Delegate tab fresh
  // even when the tab isn't open. Once per 3s.
  useEffect(() => {
    if (!activeConversationId) { setInflightCount(0); return }
    let cancelled = false
    async function refresh() {
      try {
        const r = await fetch(
          `/api/delegate/tasks?conv_id=${encodeURIComponent(activeConversationId!)}&limit=20`,
        )
        const d = await r.json() as { tasks: Array<{ status: string }> }
        if (!cancelled) {
          setInflightCount(
            (d.tasks ?? []).filter(t => t.status === 'pending' || t.status === 'running').length,
          )
        }
      } catch { /* silent */ }
    }
    void refresh()
    const h = window.setInterval(refresh, 3000)
    return () => { cancelled = true; clearInterval(h) }
  }, [activeConversationId])

  if (!voicePanelOpen) {
    return (
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="flex flex-col items-center py-4 gap-2 glass rounded-2xl flex-shrink-0"
        style={{ width: 44 }}
      >
        <button
          onClick={() => setVoicePanelOpen(true)}
          className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)] transition-colors"
          title="Open right panel"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <button
          onClick={() => { setVoicePanelOpen(true); setRightPanel('voice') }}
          className="p-2 rounded-lg text-[var(--accent)]"
          title="Voice"
        >
          <Volume2 className="w-4 h-4" />
        </button>
        <button
          onClick={() => { setVoicePanelOpen(true); setRightPanel('delegate') }}
          className="relative p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--accent)]"
          title={inflightCount > 0 ? `${inflightCount} auxiliary worker${inflightCount === 1 ? '' : 's'} running` : 'Auxiliary models'}
        >
          <Bot className="w-4 h-4" />
          {inflightCount > 0 && (
            <span className="absolute top-0.5 right-0.5 w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
          )}
        </button>
      </motion.div>
    )
  }

  return (
    <motion.div
      initial={{ width: 0, opacity: 0 }}
      animate={{ width: 320, opacity: 1 }}
      exit={{ width: 0, opacity: 0 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="flex-shrink-0 flex flex-col glass rounded-3xl overflow-hidden"
      style={{ minWidth: 320 }}
    >
      {/* Header with tab switcher */}
      <div className="flex items-center gap-1 px-2.5 py-2 border-b border-[var(--border)] bg-[var(--bg-glass-strong)]">
        <TabButton
          icon={<Volume2 className="w-3.5 h-3.5" />}
          label="Voice"
          active={active === 'voice'}
          onClick={() => setRightPanel('voice')}
        />
        <TabButton
          icon={<Bot className="w-3.5 h-3.5" />}
          label="Auxiliary"
          active={active === 'delegate'}
          badge={inflightCount}
          onClick={() => setRightPanel('delegate')}
        />
        <div className="flex-1" />
        <button
          onClick={() => setVoicePanelOpen(false)}
          className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)] transition-colors"
          title="Collapse"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-hidden flex flex-col">
        {active === 'delegate' ? <DelegatePanel /> : <VoicePanel />}
      </div>
    </motion.div>
  )
}


function TabButton({
  icon, label, active, badge, onClick,
}: {
  icon:   React.ReactNode
  label:  string
  active: boolean
  badge?: number
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={`relative flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors ${
        active
          ? 'text-[var(--text-primary)] bg-[var(--bg-tertiary)]'
          : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)]'
      }`}
    >
      <span className={active ? 'text-[var(--accent)]' : ''}>{icon}</span>
      <span className="tracking-tight">{label}</span>
      {badge != null && badge > 0 && (
        <span className="ml-0.5 text-[9px] font-mono px-1 py-0.5 rounded-full bg-amber-400/20 text-amber-300 border border-amber-400/40 min-w-[16px] text-center">
          {badge}
        </span>
      )}
    </button>
  )
}
