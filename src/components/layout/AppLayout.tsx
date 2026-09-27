import { AnimatePresence, motion } from 'framer-motion'
import { useAppStore } from '@/store/appStore'
import { Sidebar } from './Sidebar'
import { ChatWindow } from '@/components/chat/ChatWindow'
import { RightPanel } from '@/components/layout/RightPanel'
import { PanelErrorBoundary } from '@/components/ui/PanelErrorBoundary'
import { SettingsView } from '@/components/settings/SettingsView'
import { MemoryView } from '@/components/memory/MemoryView'
import { ResearchView } from '@/components/research/ResearchView'
import { ReelsView } from '@/components/reels/ReelsView'
import { DocumentsPanel } from '@/components/documents/DocumentsPanel'
import { AbletonView } from '@/components/ableton/AbletonView'
import { WorkersView } from '@/components/workers/WorkersView'
import { TasksView } from '@/components/tasks/TasksView'
import { FlowsView } from '@/components/flows/FlowsView'

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
      {/* Wallpaper (vivid per-theme mesh) → illustration softly blended → vignette → grain.
          Chat/sidebar/right-panel glass surfaces sit above via `z-10` and stay legible. */}
      <div className="atmos atmos-wallpaper" />
      <div className="atmos atmos-backdrop" />
      <div className="atmos atmos-vignette" />
      <div className="atmos atmos-grain" />

      <div className="relative z-10 flex flex-1 overflow-hidden p-3 gap-3">
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
                className="flex-1 flex overflow-hidden gap-3"
              >
                <div className="flex-1 min-w-0 overflow-hidden">
                  <PanelErrorBoundary label="Chat" resetKey={currentView} framed>
                    <ChatWindow />
                  </PanelErrorBoundary>
                </div>
                <PanelErrorBoundary label="Right panel" resetKey={currentView} framed>
                  <RightPanel />
                </PanelErrorBoundary>
              </motion.div>
            ) : currentView === 'settings' ? (
              <motion.div
                key="settings"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 min-w-0 overflow-hidden"
              >
                <PanelErrorBoundary label="Settings" resetKey={currentView} framed>
                  <SettingsView />
                </PanelErrorBoundary>
              </motion.div>
            ) : currentView === 'reels' ? (
              <motion.div
                key="reels"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 min-w-0 overflow-hidden"
              >
                <PanelErrorBoundary label="Reels" resetKey={currentView} framed>
                  <ReelsView />
                </PanelErrorBoundary>
              </motion.div>
            ) : currentView === 'documents' ? (
              <motion.div
                key="documents"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 min-w-0 overflow-hidden"
              >
                <PanelErrorBoundary label="Documents" resetKey={currentView} framed>
                  <DocumentsPanel />
                </PanelErrorBoundary>
              </motion.div>
            ) : currentView === 'music' ? (
              <motion.div
                key="music"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 min-w-0 overflow-hidden"
              >
                <PanelErrorBoundary label="Ableton" resetKey={currentView} framed>
                  <AbletonView />
                </PanelErrorBoundary>
              </motion.div>
            ) : currentView === 'research' ? (
              <motion.div
                key="research"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 min-w-0 overflow-hidden"
              >
                <PanelErrorBoundary label="Research" resetKey={currentView} framed>
                  <ResearchView />
                </PanelErrorBoundary>
              </motion.div>
            ) : currentView === 'workers' ? (
              <motion.div
                key="workers"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 min-w-0 overflow-hidden glass rounded-3xl"
              >
                <PanelErrorBoundary label="Workers" resetKey={currentView} framed>
                  <WorkersView />
                </PanelErrorBoundary>
              </motion.div>
            ) : currentView === 'tasks' ? (
              <motion.div
                key="tasks"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 min-w-0 overflow-hidden"
              >
                <PanelErrorBoundary label="Tasks" resetKey={currentView} framed>
                  <TasksView />
                </PanelErrorBoundary>
              </motion.div>
            ) : currentView === 'flows' ? (
              <motion.div
                key="flows"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 min-w-0 overflow-hidden glass rounded-3xl"
              >
                <PanelErrorBoundary label="Flows" resetKey={currentView} framed>
                  <FlowsView />
                </PanelErrorBoundary>
              </motion.div>
            ) : (
              <motion.div
                key="memory"
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }}
                className="flex-1 min-w-0 overflow-hidden"
              >
                <PanelErrorBoundary label="Memory" resetKey={currentView} framed>
                  <MemoryView />
                </PanelErrorBoundary>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}
