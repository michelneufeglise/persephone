import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronDown, Brain } from 'lucide-react'

interface ThinkingPanelProps {
  content: string
  isStreaming?: boolean
}

export function ThinkingPanel({ content, isStreaming }: ThinkingPanelProps) {
  // Auto-expand while streaming so the user sees thinking appear; collapse
  // once the final answer arrives. After that, the user controls it.
  const [open, setOpen] = useState(true)
  const [userToggled, setUserToggled] = useState(false)
  const wasStreaming = useRef(isStreaming)
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (wasStreaming.current && !isStreaming && !userToggled) {
      setOpen(false)
    }
    wasStreaming.current = isStreaming
  }, [isStreaming, userToggled])

  useEffect(() => {
    if (isStreaming && open && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight
    }
  }, [content, isStreaming, open])

  if (!content) return null

  const handleToggle = () => {
    setUserToggled(true)
    setOpen(o => !o)
  }

  return (
    <div className="mb-2 w-full">
      <button
        onClick={handleToggle}
        className="flex items-center gap-2 text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors group"
      >
        <Brain
          className={`w-3.5 h-3.5 text-[var(--accent)] ${isStreaming ? 'opacity-100 animate-pulse' : 'opacity-70'}`}
        />
        <span className="font-mono">
          {isStreaming ? 'Thinking…' : 'Reasoning'}
        </span>
        <ChevronDown
          className="w-3.5 h-3.5 transition-transform duration-200"
          style={{ transform: open ? 'rotate(180deg)' : 'rotate(0deg)' }}
        />
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: 'easeInOut' }}
            className="overflow-hidden"
          >
            <div
              ref={bodyRef}
              className="mt-2 p-3 rounded-lg bg-[var(--thinking-bg)] border border-[var(--border)] max-h-64 overflow-y-auto"
            >
              <pre className="text-xs font-mono text-[var(--text-muted)] whitespace-pre-wrap leading-relaxed">
                {content}
              </pre>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
