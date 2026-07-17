import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  User, Cpu, Boxes, Brain, Database, Wrench, Palette, Volume2, Bot,
} from 'lucide-react'
import { CharacterSection } from './sections/CharacterSection'
import { ModelSection } from './sections/ModelSection'
import { ModelRolesSection } from './sections/ModelRolesSection'
import { AuxiliarySection } from './sections/AuxiliarySection'
import { MemorySection } from './sections/MemorySection'
import { McpSection } from './sections/McpSection'
import { ThemeSection } from './sections/ThemeSection'
import { VoiceSection } from './sections/VoiceSection'

type Tab = 'character' | 'modelRoles' | 'auxiliary' | 'model' | 'voice' | 'memory' | 'mcp' | 'theme'

const TABS: { id: Tab; label: string; icon: React.ElementType }[] = [
  { id: 'character',   label: 'Character',  icon: User },
  { id: 'modelRoles',  label: 'Models',     icon: Boxes },
  { id: 'auxiliary',   label: 'Auxiliary',  icon: Bot },
  { id: 'model',       label: 'Generation', icon: Cpu },
  { id: 'voice',       label: 'Voice',      icon: Volume2 },
  { id: 'memory',      label: 'Memory',     icon: Brain },
  { id: 'mcp',         label: 'Tools',      icon: Wrench },
  { id: 'theme',       label: 'Theme',      icon: Palette },
]

export function SettingsView() {
  const [activeTab, setActiveTab] = useState<Tab>('character')

  return (
    <div className="flex h-full bg-[var(--bg-primary)] rounded-xl overflow-hidden border border-[var(--border)]">
      {/* Left nav */}
      <div className="w-44 flex-shrink-0 bg-[var(--bg-secondary)] border-r border-[var(--border)] py-4 flex flex-col gap-1 px-2">
        <div className="px-2 pb-3">
          <h2 className="font-serif text-lg text-[var(--text-primary)]">Settings</h2>
          <p className="text-xs text-[var(--text-muted)] mt-0.5">Configure Persephone</p>
        </div>
        {TABS.map(tab => {
          const Icon = tab.icon
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2.5 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 text-left
                ${activeTab === tab.id
                  ? 'bg-[var(--accent-dim)] text-[var(--accent)] border border-[var(--border-bright)]'
                  : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] hover:text-[var(--text-primary)]'
                }`}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              {tab.label}
            </button>
          )
        })}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-6" style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--scrollbar) transparent' }}>
        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            initial={{ opacity: 0, x: 10 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -10 }}
            transition={{ duration: 0.2 }}
          >
            {activeTab === 'character'  && <CharacterSection />}
            {activeTab === 'modelRoles' && <ModelRolesSection />}
            {activeTab === 'auxiliary'  && <AuxiliarySection />}
            {activeTab === 'model'      && <ModelSection />}
            {activeTab === 'voice'      && <VoiceSection />}
            {activeTab === 'memory'     && <MemorySection />}
            {activeTab === 'mcp'        && <McpSection />}
            {activeTab === 'theme'      && <ThemeSection />}
          </motion.div>
        </AnimatePresence>
      </div>
    </div>
  )
}
