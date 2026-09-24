import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { TileCard } from './TileCard'
import type { Tile } from '@/lib/docAgent'

interface FlowPanelProps {
  tiles: Tile[]
  running: boolean
  title?: string
  hideHeader?: boolean
}

export function FlowPanel({ tiles, running, title = 'Model flow', hideHeader = false }: FlowPanelProps) {
  const [now, setNow] = useState(Date.now())
  const containerRef = useRef<HTMLDivElement>(null)
  const lastTileRef = useRef<HTMLDivElement>(null)
  const intervalRef = useRef<NodeJS.Timeout | null>(null)

  // Update 'now' every 250ms while running
  useEffect(() => {
    if (!running) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      return
    }

    // Set immediately when running becomes true
    setNow(Date.now())

    intervalRef.current = setInterval(() => {
      setNow(Date.now())
    }, 250)

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
    }
  }, [running])

  // Auto-scroll to newest tile while running
  useEffect(() => {
    if (!running || !lastTileRef.current || !containerRef.current) return

    // Use requestAnimationFrame to ensure the DOM has updated
    const raf = requestAnimationFrame(() => {
      lastTileRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    })

    return () => cancelAnimationFrame(raf)
  }, [tiles.length, running])

  return (
    <div className="h-full flex flex-col bg-[var(--bg-secondary)]/40 border-l border-[var(--border)] overflow-hidden">
      {/* Header (hidden if hideHeader is true, but keep Running… indicator visible) */}
      {!hideHeader && (
        <div className="flex-shrink-0 px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
          <h3 className="text-sm font-medium text-[var(--text-primary)]">{title}</h3>
          {running && (
            <div className="flex items-center gap-2">
              <motion.div
                className="w-2 h-2 rounded-full bg-[var(--accent)]"
                animate={{ opacity: [1, 0.4, 1] }}
                transition={{ duration: 1.5, repeat: Infinity }}
              />
              <span className="text-xs text-[var(--text-muted)]">Running…</span>
            </div>
          )}
        </div>
      )}

      {/* Running indicator for collapsed view (shown even when header is hidden) */}
      {hideHeader && running && (
        <div className="flex-shrink-0 px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <motion.div
              className="w-2 h-2 rounded-full bg-[var(--accent)]"
              animate={{ opacity: [1, 0.4, 1] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            />
            <span className="text-xs text-[var(--text-muted)]">Running…</span>
          </div>
        </div>
      )}

      {/* Empty state */}
      {tiles.length === 0 && !running && (
        <div className="flex-1 flex items-center justify-center px-4 text-center">
          <div className="text-sm text-[var(--text-muted)]">
            Ask something — you'll see how Laya routes it and which models run.
          </div>
        </div>
      )}

      {/* Tiles container */}
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto px-4 py-4 space-y-2"
        style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--scrollbar) transparent' }}
      >
        <AnimatePresence initial={false}>
          {tiles.map((tile, idx) => (
            <motion.div
              key={tile.id}
              ref={idx === tiles.length - 1 ? lastTileRef : null}
              className="relative"
              initial={{ opacity: 0, y: -14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
            >
              <TileCard tile={tile} now={now} />

              {/* Connector line between tiles */}
              {idx < tiles.length - 1 && (
                <motion.div
                  className="absolute left-1/2 -translate-x-1/2 -bottom-2 w-0.5 h-2 bg-gradient-to-b from-[var(--border)] to-transparent"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
                />
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}
