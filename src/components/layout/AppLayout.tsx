import { AnimatePresence, motion } from 'framer-motion'
import { useAppStore } from '@/store/appStore'
import { Sidebar } from './Sidebar'
import { ChatWindow } from '@/components/chat/ChatWindow'
import { RightPanel } from '@/components/layout/RightPanel'
import { SettingsView } from '@/components/settings/SettingsView'
import { MemoryView } from '@/components/memory/MemoryView'
import { ResearchView } from '@/components/research/ResearchView'

export function AppLayout() {
  const { currentView } = useAppStore()

  return (
    <div className="relative flex h-screen w-screen overflow-hidden bg-[var(--bg-primary)]">
      {/* ── Atmospheric layers (fixed under shell) ─────────────────────── */}
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
