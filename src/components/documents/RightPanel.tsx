import { useEffect, useState, useRef } from 'react'
import { motion } from 'framer-motion'
import { Workflow, ChevronsRight, ChevronsLeft } from 'lucide-react'
import { clsx } from 'clsx'
import { FlowPanel } from './FlowPanel'
import { PanelErrorBoundary } from '@/components/ui/PanelErrorBoundary'
import type { Tile } from '@/lib/docAgent'

interface RightPanelProps {
  // FlowPanel props
  tiles: Tile[]
  running: boolean

  // Controlled collapsed state
  collapsed: boolean
  onCollapsedChange: (collapsed: boolean) => void

  // Optional: force visibility (for mobile drawer)
  forceVisible?: boolean
}

export function RightPanel({
  tiles,
  running,
  collapsed,
  onCollapsedChange,
  forceVisible = false,
}: RightPanelProps) {
  const [mounted, setMounted] = useState(false)

  // Load persisted state from localStorage if needed
  useEffect(() => {
    setMounted(true)
  }, [])

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
          onClick={() => onCollapsedChange(false)}
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
              onClick={() => onCollapsedChange(false)}
              className={clsx(
                'p-2.5 rounded-lg transition-colors',
                'text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--glass-fill-hover)]'
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
        </div>
      </motion.div>
    )
  }

  // Expanded state: full panel with only Flow
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
      {/* Header with title and collapse button */}
      <div className="flex-shrink-0 px-4 py-3 border-b border-[var(--glass-stroke)]  flex items-center justify-between gap-2">
        {/* Title */}
        <div className="flex items-center gap-1.5">
          <Workflow className="w-4 h-4 text-[var(--accent)]" />
          <span className="text-xs font-medium uppercase tracking-wider text-[var(--text-secondary)]">Model flow</span>
        </div>

        {/* Collapse button */}
        <button
          onClick={() => onCollapsedChange(true)}
          className="flex-shrink-0 p-1 text-[var(--text-muted)] hover:text-[var(--accent)] rounded-md hover:bg-[var(--glass-fill-hover)] transition-colors"
          title="Collapse panel"
        >
          <ChevronsRight className="w-4 h-4" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden h-full min-h-0">
        <PanelErrorBoundary label="Model flow" resetKey="flow">
          <FlowPanel tiles={tiles} running={running} hideHeader />
        </PanelErrorBoundary>
      </div>
    </motion.div>
  )
}
