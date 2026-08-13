import { useState, useRef, useEffect } from 'react'
import { Send, Square, Paperclip, X } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore } from '@/store/appStore'

interface ChatInputProps {
  onSend: (text: string, files?: File[]) => void
  onStop: () => void
}

export function ChatInput({ onSend, onStop }: ChatInputProps) {
  const [value, setValue] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // Gate ONLY on the active tab. If a different tab is streaming, this
  // one should still accept a new message so the user can compare in
  // parallel — the whole point of the tab strip.
  //
  // Subscribe directly to the two slices we care about so React re-renders
  // when generatingConvs changes; a plain isConvGenerating() call wouldn't
  // trigger a subscription.
  const activeConversationId = useAppStore(s => s.activeConversationId)
  const generatingConvs      = useAppStore(s => s.generatingConvs)
  const isGenerating = !!activeConversationId && generatingConvs.includes(activeConversationId)

  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 200)}px`
  }, [value])

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  function handleSend() {
    const text = value.trim()
    if ((!text && files.length === 0) || isGenerating) return
    onSend(text, files.length > 0 ? files : undefined)
    setValue('')
    setFiles([])
  }

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const selectedFiles = Array.from(e.target.files || [])
    const newFiles = [...files]
    for (const file of selectedFiles) {
      const exists = newFiles.some(f => f.name === file.name && f.size === file.size)
      if (!exists) newFiles.push(file)
    }
    setFiles(newFiles)
    // Reset input so selecting the same file again works
    e.target.value = ''
  }

  function removeFile(index: number) {
    setFiles(files.filter((_, i) => i !== index))
  }

  return (
    <div className="flex flex-col p-4 border-t border-[var(--border)] bg-[var(--bg-glass-strong)] rounded-b-3xl">
      {/* File chips row — shown when files are selected */}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3">
          {files.map((file, i) => (
            <div key={i} className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-[var(--bg-tertiary)] border border-[var(--border)]">
              <span className="text-xs text-[var(--text-primary)] max-w-[200px] truncate">{file.name}</span>
              <button
                onClick={() => removeFile(i)}
                className="flex-shrink-0 text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
                title="Remove file"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Hidden file input */}
      <input
        type="file"
        ref={fileRef}
        className="hidden"
        onChange={handleFileSelect}
        accept="image/*,.pdf,.docx,.doc,.xlsx,.csv,.txt,.md,.rtf,.pptx,.odt,.html,.htm,.json"
        multiple
      />

      {/* hairline gradient above the input — implies depth */}
      <span
        className="absolute left-6 right-6 -top-[0.5px] h-[1px] pointer-events-none"
        style={{ background: 'linear-gradient(90deg, transparent, var(--border-bright), transparent)' }}
      />

      {/* Input row with textarea, attach button, and send/stop */}
      <div className="relative flex items-end gap-2">
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

        {/* Attach button */}
        <button
          onClick={() => fileRef.current?.click()}
          className="flex-shrink-0 w-12 h-12 rounded-2xl flex items-center justify-center transition-all duration-200"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-muted)',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--accent)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-muted)')}
          title="Attach files or images"
        >
          <Paperclip className="w-4 h-4" />
        </button>

        <AnimatePresence mode="wait">
          {isGenerating ? (
            <motion.button
              key="stop"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.8, opacity: 0 }}
              onClick={onStop}
              className="flex-shrink-0 w-12 h-12 rounded-2xl text-white flex items-center justify-center transition-all duration-200"
              style={{
                background: 'linear-gradient(135deg, #ef4444, #b91c1c)',
                boxShadow: '0 8px 22px -8px rgba(239,68,68,0.6), inset 0 1px 0 rgba(255,255,255,0.2)',
              }}
              title="Stop generation"
            >
              <Square className="w-4 h-4 fill-current" />
            </motion.button>
          ) : (
            <motion.button
              key="send"
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.8, opacity: 0 }}
              whileHover={{ scale: (value.trim() || files.length > 0) ? 1.04 : 1 }}
              whileTap={{ scale: 0.96 }}
              onClick={handleSend}
              disabled={!value.trim() && files.length === 0}
              className="flex-shrink-0 w-12 h-12 rounded-2xl text-white flex items-center justify-center
                transition-all duration-300 disabled:opacity-40 disabled:cursor-not-allowed"
              style={{
                background: 'linear-gradient(135deg, var(--accent), var(--accent-deep))',
                boxShadow: (value.trim() || files.length > 0)
                  ? '0 8px 22px -8px var(--accent-glow), 0 0 28px -6px var(--accent-glow), inset 0 1px 0 rgba(255,255,255,0.2)'
                  : 'inset 0 1px 0 rgba(255,255,255,0.06)',
              }}
              title="Send (Enter)"
            >
              <Send className="w-4 h-4" />
            </motion.button>
          )}
        </AnimatePresence>
      </div>
    </div>
  )
}
