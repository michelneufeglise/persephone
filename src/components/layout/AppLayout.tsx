import { AnimatePresence, motion } from 'framer-motion'
import { useAppStore } from '@/store/appStore'
import { Sidebar } from './Sidebar'
import { ChatWindow } from '@/components/chat/ChatWindow'
import { RightPanel } from '@/components/layout/RightPanel'
import { SettingsView } from '@/components/settings/SettingsView'
import { MemoryView } from '@/components/memory/MemoryView'
import { ResearchView } from '@/components/research/ResearchView'
import { ReelsView } from '@/components/reels/ReelsView'
import { DocumentsPanel } from '@/components/documents/DocumentsPanel'
import { AbletonView } from '@/components/ableton/AbletonView'
import { WorkersView } from '@/components/workers/WorkersView'

export function AppLayout() {
  const { currentView } = useAppStore()

  return (
    <div className="relative flex h-screen w-screen overflow-hidden bg-[var(--bg-primary)]">
      {/* Electron drag strip — invisible 28px band across the top of the
          window. Lets the user drag the window from anywhere along it,
          not just the tiny area around the macOS traffic-light buttons.
          Sits BELOW the traffic lights (they get natural click precedence
          via the OS) and ABOVE the app content (via z-50). */}
      <div
        className="window-drag pointer-events-auto fixed top-0 left-0 right-0 h-7 z-50"
        aria-hidden
      />

      {/* ── Atmospheric layers (fixed under shell) ─────────────────────── */}
      {/* Backdrop illustration sits DEEPEST — the aurora / vignette / grain
          stack on top of it so the artwork feels absorbed into the theme
          rather than pasted on. Chat/sidebar/right-panel glass surfaces
          sit above via `z-10` and stay legible via backdrop-blur. */}
      <div className="atmos atmos-backdrop" />
      <div className="atmos atmos-aurora" />
      <div className="atmos atmos-vignette" />
      <div className="atmos atmos-grain" />

      {/* Subtle scanline / horizon line near the top */}
      <div
        className="atmos"
        style={{
          background:
            'linear-gradient(180deg, transparent 0, var(--border) 1px, transparent 2px), linear-gradient(180deg, var(--accent-dim) 0%, transparent 12%)',
          opacity: 0.35,
          height: '120px',
        }}
      />

      <div className="relative z-10 flex flex-1 overflow-hidden">
        <Sidebar />

        <div className="flex-1 flex overflow-hidden relative">
          <AnimatePresence mode="wait">
            {currentView === 'chat' ? (
              <motion.div
                key="chat"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 flex overflow-hidden p-4 gap-4"
              >
                <div className="flex-1 overflow-hidden">
                  <ChatWindow />
                </div>
                <RightPanel />
              </motion.div>
            ) : currentView === 'settings' ? (
              <motion.div
                key="settings"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 p-4"
              >
                <SettingsView />
              </motion.div>
            ) : currentView === 'reels' ? (
              <motion.div
                key="reels"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 p-4"
              >
                <ReelsView />
              </motion.div>
            ) : currentView === 'documents' ? (
              <motion.div
                key="documents"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 overflow-hidden"
              >
                <DocumentsPanel />
              </motion.div>
            ) : currentView === 'music' ? (
              <motion.div
                key="music"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 p-4"
              >
                <AbletonView />
              </motion.div>
            ) : currentView === 'research' ? (
              <motion.div
                key="research"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 p-4"
              >
                <ResearchView />
              </motion.div>
            ) : currentView === 'workers' ? (
              <motion.div
                key="workers"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 overflow-hidden"
              >
                <WorkersView />
              </motion.div>
            ) : (
              <motion.div
                key="memory"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 p-4"
              >
                <MemoryView />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}
