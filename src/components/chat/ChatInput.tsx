import { useState, useRef, useEffect } from 'react'
import { Send, Square, Bot } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore } from '@/store/appStore'

interface ChatInputProps {
  onSend: (text: string) => void
  onSendToWorker: (text: string) => void
  onStop: () => void
}

export function ChatInput({ onSend, onSendToWorker, onStop }: ChatInputProps) {
  const [value, setValue] = useState('')
  const [sendingToWorker, setSendingToWorker] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const { isGenerating } = useAppStore()

  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`
  }, [value])

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      // Cmd/Ctrl+Enter → send to worker; plain Enter → send to chat.
      if (e.metaKey || e.ctrlKey) handleSendToWorker()
      else                        handleSend()
    }
  }

  function handleSend() {
    const text = value.trim()
    if (!text || isGenerating) return
    setValue('')
    onSend(text)
  }

  async function handleSendToWorker() {
    const text = value.trim()
    if (!text || sendingToWorker) return
    setSendingToWorker(true)
    setValue('')
    try {
      onSendToWorker(text)
    } finally {
      // Small delay so the button flash is visible even on instant dispatch.
      setTimeout(() => setSendingToWorker(false), 300)
    }
  }

  return (
    <div className="relative flex items-end gap-2 p-4 border-t border-[var(--border)] bg-[var(--bg-glass-strong)] rounded-b-3xl">
      {/* hairline gradient above the input — implies depth */}
      <span
        className="absolute left-6 right-6 -top-[0.5px] h-[1px] pointer-events-none"
        style={{ background: 'linear-gradient(90deg, transparent, var(--border-bright), transparent)' }}
      />

      <div className="flex-1 relative">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask Persephone anything…"
          rows={1}
          className="w-full resize-none rounded-2xl border border-[var(--border)]
            px-4 py-3 text-[14px] text-[var(--text-primary)] leading-relaxed
            placeholder:text-[var(--text-muted)] focus:outline-none
            transition-all duration-300 max-h-[200px] overflow-y-auto
            focus:border-[var(--accent)] focus:shadow-[0_0_0_3px_var(--accent-dim)]"
          style={{
            background: 'var(--bg-primary)',
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.04), inset 0 2px 12px rgba(0,0,0,0.25)',
            scrollbarWidth: 'thin',
          }}
        />
      </div>

      <AnimatePresence mode="wait">
        {isGenerating ? (
          <motion.button
            key="stop"
            initial={{ scale: 0.8, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.8, opacity: 0 }}
            onClick={onStop}
            className="flex-shrink-0 w-11 h-11 rounded-2xl text-white flex items-center justify-center transition-all duration-200"
            style={{
              background: 'linear-gradient(135deg, #ef4444, #b91c1c)',
              boxShadow: '0 8px 22px -8px rgba(239,68,68,0.6), inset 0 1px 0 rgba(255,255,255,0.2)',
            }}
            title="Stop generation"
          >
            <Square className="w-4 h-4 fill-current" />
          </motion.button>
        ) : (
          <motion.div
            key="send-buttons"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="flex-shrink-0 flex items-center gap-1.5"
          >
            {/* Send to worker */}
            <motion.button
              whileHover={{ scale: value.trim() ? 1.04 : 1 }}
              whileTap={{ scale: 0.96 }}
              onClick={handleSendToWorker}
              disabled={!value.trim() || sendingToWorker}
              className={`w-11 h-11 rounded-2xl flex items-center justify-center transition-all duration-300 border disabled:opacity-40 disabled:cursor-not-allowed ${
                sendingToWorker
                  ? 'border-amber-400 bg-amber-400/20 text-amber-300'
                  : 'border-amber-400/40 bg-amber-400/10 text-amber-300 hover:bg-amber-400/20'
              }`}
              title="Send to auxiliary worker model (⌘/Ctrl+Enter). Judge picks the best fit; runs in background; result appears in right panel."
            >
              <Bot className="w-4 h-4" />
            </motion.button>

            {/* Send to main chat */}
            <motion.button
              whileHover={{ scale: value.trim() ? 1.04 : 1 }}
              whileTap={{ scale: 0.96 }}
              onClick={handleSend}
              disabled={!value.trim()}
              className="w-11 h-11 rounded-2xl text-white flex items-center justify-center
                transition-all duration-300 disabled:opacity-40 disabled:cursor-not-allowed"
              style={{
                background: 'linear-gradient(135deg, var(--accent), var(--accent-deep))',
                boxShadow: value.trim()
                  ? '0 8px 22px -8px var(--accent-glow), 0 0 28px -6px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.2)'
                  : 'inset 0 1px 0 rgba(255,255,255,0.06)',
              }}
              title="Send to main chat (Enter)"
            >
              <Send className="w-4 h-4" />
            </motion.button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
