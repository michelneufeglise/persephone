import { useState, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Search, Library, ChevronRight } from 'lucide-react'
import type { FlowNodeType } from '@/types/flows'
import { nodeMetadata } from './nodeMeta'

interface NodeLibraryProps {
  onDragStart: (nodeType: FlowNodeType) => void
  onAddNode: (type: FlowNodeType) => void
}

export function NodeLibrary({ onDragStart, onAddNode }: NodeLibraryProps) {
  const [isOpen, setIsOpen] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')

  const nodeTypes = Object.entries(nodeMetadata) as Array<[FlowNodeType, typeof nodeMetadata[FlowNodeType]]>

  // Filter and group nodes
  const groupedNodes = useMemo(() => {
    const filtered = nodeTypes.filter(
      ([_, meta]) =>
        meta.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
        meta.description.toLowerCase().includes(searchQuery.toLowerCase())
    )

    const groups = new Map<string, typeof nodeTypes>()
    filtered.forEach(([type, meta]) => {
      if (!groups.has(meta.category)) {
        groups.set(meta.category, [])
      }
      groups.get(meta.category)!.push([type, meta])
    })

    return groups
  }, [searchQuery])

  const handleDragStart = (nodeType: FlowNodeType) => (e: React.DragEvent) => {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('application/reactflow', nodeType)
    onDragStart(nodeType)
  }

  return (
    <>
      {/* Toggle button for collapsed state */}
      <AnimatePresence>
        {!isOpen && (
          <motion.button
            key="toggle-open"
            initial={{ opacity: 0, x: 10 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 10 }}
            onClick={() => setIsOpen(true)}
            className="absolute right-4 top-1/2 -translate-y-1/2 z-20 p-2 rounded-lg bg-[var(--bg-glass)] border border-[var(--border-glass)] hover:bg-[var(--accent-dim)] text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors group"
            title="Open library"
          >
            <Library className="w-5 h-5" />
          </motion.button>
        )}
      </AnimatePresence>

      {/* Drawer panel */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            key="drawer"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ type: 'spring', damping: 20, stiffness: 300 }}
            className="absolute right-0 top-0 bottom-0 w-72 glass rounded-l-2xl flex flex-col shadow-deep z-20 overflow-hidden"
          >
            {/* Header */}
            <div className="px-4 py-3 border-b border-[var(--border)] bg-[var(--bg-glass-strong)] flex items-center justify-between gap-2 flex-shrink-0">
              <div className="flex items-center gap-2 flex-1 min-w-0">
                <Library className="w-4 h-4 text-[var(--accent)] flex-shrink-0" />
                <h3 className="text-xs font-semibold text-[var(--text-primary)]">Node Library</h3>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                className="p-1 rounded hover:bg-[var(--accent-dim)] text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors flex-shrink-0"
                title="Collapse library"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>

            {/* Search input */}
            <div className="px-4 py-3 border-b border-[var(--border)] flex-shrink-0">
              <div className="relative">
                <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-4 h-4 text-[var(--text-muted)] pointer-events-none" />
                <input
                  type="text"
                  placeholder="Search nodes…"
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  className="w-full pl-8 pr-3 py-1.5 rounded-lg bg-[var(--bg-secondary)] text-xs text-[var(--text-primary)] border border-[var(--border)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--accent)] transition-all"
                />
              </div>
            </div>

            {/* Nodes list */}
            <div className="flex-1 overflow-y-auto p-3 space-y-3">
              {Array.from(groupedNodes.entries()).map(([category, nodes]) => (
                <div key={category} className="space-y-1.5">
                  {/* Category header */}
                  <h4 className="text-xs font-semibold text-[var(--text-secondary)] px-2 pt-1">
                    {category}
                  </h4>

                  {/* Node cards */}
                  <div className="space-y-1.5">
                    {nodes.map(([type, meta]) => {
                      const Icon = meta.icon
                      return (
                        <div
                          key={type}
                          draggable
                          onDragStart={handleDragStart(type)}
                          onClick={() => onAddNode(type)}
                          className="group p-3 rounded-lg bg-[var(--bg-secondary)] border border-[var(--border)] hover:border-[var(--border-bright)] cursor-move transition-all hover:shadow-lg hover:translate-y-[-4px] hover:translate-x-[4px] active:scale-95"
                          style={{
                            borderLeft: `2px solid ${meta.color}`,
                          }}
                        >
                          <div className="flex items-start gap-2.5">
                            <div
                              className="flex items-center justify-center w-6 h-6 rounded-lg flex-shrink-0 mt-0.5"
                              style={{
                                background: `rgba(${
                                  meta.color.startsWith('var(')
                                    ? '214, 53, 106'
                                    : meta.color.match(/\d+/g)?.slice(0, 3).join(', ') || '214, 53, 106'
                                }, 0.2)`,
                              }}
                            >
                              <div style={{ color: meta.color }}>
                                <Icon className="w-3.5 h-3.5" />
                              </div>
                            </div>
                            <div className="flex-1 min-w-0">
                              <p className="text-xs font-semibold text-[var(--text-primary)]">
                                {meta.label}
                              </p>
                              <p className="text-xs text-[var(--text-muted)] mt-0.5">
                                {meta.description}
                              </p>
                            </div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>
              ))}

              {/* Empty state */}
              {groupedNodes.size === 0 && (
                <div className="text-center py-8 text-[var(--text-muted)]">
                  <p className="text-xs">No nodes found</p>
                </div>
              )}
            </div>

            {/* Footer hint */}
            <div className="px-4 py-2 border-t border-[var(--border)] text-xs text-[var(--text-muted)] text-center flex-shrink-0 bg-[var(--bg-glass-strong)]">
              Drag or click to add
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  )
}
