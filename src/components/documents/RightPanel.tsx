import { useEffect, useState, useRef } from 'react'
import { motion } from 'framer-motion'
import { Workflow, Network, ChevronsRight, ChevronsLeft } from 'lucide-react'
import { clsx } from 'clsx'
import { FlowPanel } from './FlowPanel'
import { KnowledgeGraph } from './KnowledgeGraph.tsx'
import type { Tile, DocConversationSummary } from '@/lib/docAgent'
import type { Message } from '@/types'

interface RightPanelProps {
  // FlowPanel props
  tiles: Tile[]
  running: boolean

  // KnowledgeGraph props
  conversations: DocConversationSummary[]
  currentConversationId: string
  currentMessages: Message[]
  selectedMessageId: string | null
  onSelectMessage: (id: string) => void

  // Controlled collapsed state
  collapsed: boolean
  onCollapsedChange: (collapsed: boolean) => void

  // Optional: force visibility (for mobile drawer)
  forceVisible?: boolean
}

type RightTab = 'flow' | 'graph'

export function RightPanel({
  tiles,
  running,
  conversations,
  currentConversationId,
  currentMessages,
  selectedMessageId,
  onSelectMessage,
  collapsed,
  onCollapsedChange,
  forceVisible = false,
}: RightPanelProps) {
  const [activeTab, setActiveTab] = useState<RightTab>('flow')
  const [mounted, setMounted] = useState(false)

  // Load persisted active tab from localStorage
  useEffect(() => {
    try {
      const savedTab = localStorage.getItem('persephone-docs-right-tab')
      if (savedTab === 'flow' || savedTab === 'graph') {
        setActiveTab(savedTab as RightTab)
      }
    } catch {}

    setMounted(true)
  }, [])

  // Save active tab to localStorage
  const handleTabChange = (tab: RightTab) => {
    setActiveTab(tab)
    try {
      localStorage.setItem('persephone-docs-right-tab', tab)
    } catch {}
  }

  // Toggle collapsed state
  const handleCollapseToggle = () => {
    onCollapsedChange(!collapsed)
  }

  if (!mounted) return null

  // Collapsed state: slim vertical strip (desktop only, not on mobile)
  const shouldShowCollapsed = collapsed && !forceVisible
  if (shouldShowCollapsed) {
    return (
      <motion.div
        initial={{ width: 44 }}
        animate={{ width: 44 }}
        transition={{ duration: 0.22 }}
        className={clsx(
          'flex flex-col w-11 flex-shrink-0 items-stretch',
          !forceVisible && 'hidden lg:flex'
        )}
      >
        {/* Expand button at top */}
        <button
          onClick={handleCollapseToggle}
          className="flex-shrink-0 p-2.5 text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
          title="Expand panel"
        >
          <ChevronsLeft className="w-4 h-4" />
        </button>

        {/* Icon buttons for tabs */}
        <div className="flex-1 flex flex-col items-center gap-2 py-2">
          {/* Flow button with pulsing dot while running */}
          <div className="relative">
            <button
              onClick={() => {
                handleTabChange('flow')
                onCollapsedChange(false)
              }}
              className={clsx(
                'p-2.5 rounded-lg transition-colors',
                activeTab === 'flow'
                  ? 'text-[var(--accent)] bg-[var(--accent-dim)]'
                  : 'text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--bg-secondary)]'
              )}
              title="Model flow"
            >
              <Workflow className="w-4 h-4" />
            </button>

            {/* Pulsing dot while generating */}
            {running && (
              <motion.div
                className="absolute bottom-1.5 right-1.5 w-2 h-2 rounded-full bg-[var(--accent)]"
                animate={{ opacity: [1, 0.4, 1] }}
                transition={{ duration: 1.5, repeat: Infinity }}
              />
            )}
          </div>

          {/* Graph button */}
          <button
            onClick={() => {
              handleTabChange('graph')
              onCollapsedChange(false)
            }}
            className={clsx(
              'p-2.5 rounded-lg transition-colors',
              activeTab === 'graph'
                ? 'text-[var(--accent)] bg-[var(--accent-dim)]'
                : 'text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--bg-secondary)]'
            )}
            title="Knowledge graph"
          >
            <Network className="w-4 h-4" />
          </button>
        </div>
      </motion.div>
    )
  }

  // Expanded state: full panel with tabs
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.18 }}
      className={clsx(
        // Width is owned by the parent card (resizable); fill it.
        'flex flex-col w-full min-w-0 overflow-hidden h-full',
        !forceVisible && 'hidden lg:flex'
      )}
    >
      {/* Header with tab control and collapse button */}
      <div className="flex-shrink-0 px-4 py-3 border-b border-[var(--border)] bg-[var(--bg-secondary)]/40 flex items-center justify-between gap-2">
        {/* Segmented control */}
        <div className="flex gap-1 flex-1">
          <button
            onClick={() => handleTabChange('flow')}
            className={clsx(
              'flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors',
              activeTab === 'flow'
                ? 'bg-[var(--accent-dim)] text-[var(--accent)]'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            )}
          >
            <Workflow className="w-3.5 h-3.5" />
            Model flow
          </button>
          <button
            onClick={() => handleTabChange('graph')}
            className={clsx(
              'flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors',
              activeTab === 'graph'
                ? 'bg-[var(--accent-dim)] text-[var(--accent)]'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            )}
          >
            <Network className="w-3.5 h-3.5" />
            Knowledge graph
          </button>
        </div>

        {/* Collapse button */}
        <button
          onClick={handleCollapseToggle}
          className="flex-shrink-0 p-1 text-[var(--text-muted)] hover:text-[var(--accent)] rounded-md hover:bg-[var(--bg-tertiary)] transition-colors"
          title="Collapse panel"
        >
          <ChevronsRight className="w-4 h-4" />
        </button>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-hidden h-full min-h-0">
        {activeTab === 'flow' ? (
          <FlowPanel tiles={tiles} running={running} hideHeader />
        ) : (
          <KnowledgeGraph
            conversations={conversations}
            currentConversationId={currentConversationId}
            currentMessages={currentMessages}
            selectedMessageId={selectedMessageId}
            onSelectMessage={onSelectMessage}
          />
        )}
      </div>
    </motion.div>
  )
}
