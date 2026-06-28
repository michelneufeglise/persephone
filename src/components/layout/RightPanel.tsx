import { AnimatePresence, motion } from 'framer-motion'
import { Volume2, FileText, ChevronRight, ChevronLeft } from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { VoicePanel } from '@/components/voice/VoicePanel'
import { DocumentsPanel } from '@/components/documents/DocumentsPanel'
import { clsx } from 'clsx'

export function RightPanel() {
  const { rightPanel, setRightPanel, voicePanelOpen, setVoicePanelOpen } = useAppStore()

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
          title="Open panel"
        >
          <ChevronLeft className="w-4 h-4" />
        </button>
        <button
          onClick={() => { setRightPanel('voice'); setVoicePanelOpen(true) }}
          className={clsx('p-2 rounded-lg transition-colors',
            rightPanel === 'voice' ? 'text-[var(--accent)]' : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]'
          )}
          title="Voice"
        >
          <Volume2 className="w-4 h-4" />
        </button>
        <button
          onClick={() => { setRightPanel('documents'); setVoicePanelOpen(true) }}
          className={clsx('p-2 rounded-lg transition-colors',
            rightPanel === 'documents' ? 'text-[var(--accent)]' : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]'
          )}
          title="Documents"
        >
          <FileText className="w-4 h-4" />
        </button>
      </motion.div>
    )
  }

  return (
    <motion.div
      initial={{ width: 0, opacity: 0 }}
      animate={{ width: rightPanel === 'documents' ? 340 : 300, opacity: 1 }}
      exit={{ width: 0, opacity: 0 }}
      transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="flex-shrink-0 flex flex-col glass rounded-3xl overflow-hidden"
      style={{ minWidth: rightPanel === 'documents' ? 340 : 300 }}
    >
      {/* Tab switcher */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-[var(--border)] bg-[var(--bg-glass-strong)]">
        <div className="flex gap-1">
          <TabBtn
            active={rightPanel === 'voice'}
            onClick={() => setRightPanel('voice')}
            icon={Volume2}
            label="Voice"
          />
          <TabBtn
            active={rightPanel === 'documents'}
            onClick={() => setRightPanel('documents')}
            icon={FileText}
            label="Documents"
          />
        </div>
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
        <AnimatePresence mode="wait">
          {rightPanel === 'voice' ? (
            <motion.div
              key="voice"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex-1 overflow-hidden flex flex-col"
            >
              <VoicePanelContent />
            </motion.div>
          ) : (
            <motion.div
              key="docs"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex-1 overflow-hidden flex flex-col"
            >
              <DocumentsPanel />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  )
}

function TabBtn({ active, onClick, icon: Icon, label }: { active: boolean; onClick: () => void; icon: React.ElementType; label: string }) {
  return (
    <button
      onClick={onClick}
      className={clsx(
        'relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all duration-300',
        active
          ? 'text-[var(--text-primary)] bg-[var(--bg-tertiary)] shadow-[var(--shadow-soft)]'
          : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]/50'
      )}
    >
      {active && (
        <span
          className="absolute -bottom-[2px] left-3 right-3 h-[2px] rounded-full"
          style={{
            background: 'linear-gradient(90deg, var(--accent), var(--holo))',
            boxShadow: '0 0 8px var(--accent-glow)',
          }}
        />
      )}
      <Icon className={clsx('w-3.5 h-3.5', active && 'text-[var(--accent)]')} />
      <span className="tracking-tight">{label}</span>
    </button>
  )
}

// Inline minimal VoicePanel for the new layout (uses the existing panel without its own header)
function VoicePanelContent() {
  return <VoicePanel />
}
