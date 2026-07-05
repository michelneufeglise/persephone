import { motion } from 'framer-motion'
import { Volume2, ChevronRight, ChevronLeft } from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { VoicePanel } from '@/components/voice/VoicePanel'

/**
 * Right-hand panel for the chat view.
 *
 * Historically shared space between Voice and Documents. Documents now has
 * its own sidebar tab (rendered full-width in AppLayout), so this panel is
 * voice-only. The `rightPanel` store field is retained but is functionally
 * fixed to 'voice' — kept only so existing persisted settings don't crash
 * a returning user.
 */
export function RightPanel() {
  const { voicePanelOpen, setVoicePanelOpen } = useAppStore()

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
          title="Open voice panel"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <button
          onClick={() => setVoicePanelOpen(true)}
          className="p-2 rounded-lg text-[var(--accent)]"
          title="Voice"
        >
          <Volume2 className="w-4 h-4" />
        </button>
      </motion.div>
    )
  }

  return (
    <motion.div
      initial={{ width: 0, opacity: 0 }}
      animate={{ width: 300, opacity: 1 }}
      exit={{ width: 0, opacity: 0 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="flex-shrink-0 flex flex-col glass rounded-3xl overflow-hidden"
      style={{ minWidth: 300 }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-[var(--border)] bg-[var(--bg-glass-strong)]">
        <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-[var(--text-primary)] bg-[var(--bg-tertiary)]">
          <Volume2 className="w-3.5 h-3.5 text-[var(--accent)]" />
          <span className="tracking-tight">Voice</span>
        </div>
        <button
          onClick={() => setVoicePanelOpen(false)}
          className="p-1.5 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-secondary)] transition-colors"
          title="Collapse"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
      </div>

      {/* Body — voice always. Documents lives in its own sidebar tab now. */}
      <div className="flex-1 overflow-hidden flex flex-col">
        <VoicePanel />
      </div>
    </motion.div>
  )
}
