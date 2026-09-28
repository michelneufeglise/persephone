import { useState, useRef, useEffect, useLayoutEffect } from 'react'
import { ArrowUp, Square, Paperclip, X, Volume2, VolumeX } from 'lucide-react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAppStore } from '@/store/appStore'
import { setVoiceEnabled, stopSpeaking } from '@/lib/speech'
import type { SendOpts } from '@/types'

interface ChatInputProps {
  onSend: (text: string, files?: File[], opts?: SendOpts) => void | Promise<void>
  onStop: () => void
  isGenerating?: boolean
  placeholder?: string
  accept?: string
  enableRoles?: boolean
  largePasteChars?: number
  /** Pre-fill the textarea; bump `nonce` to re-apply. `select` = substring to pre-select. */
  prefill?: { text: string; nonce: number; select?: string } | null
  /** Show the voice (TTS) on/off button inside the bar. Default true. */
  showVoiceToggle?: boolean
}

/** One text line: 20px line-height + 8px top/bottom padding = the 36px button size. */
const LINE_BOX_PX = 36
const MAX_TEXTAREA_PX = 200

export function ChatInput({
  onSend,
  onStop,
  isGenerating: isGeneratingProp,
  placeholder = 'Ask Persephone anything…',
  accept = 'image/*,.pdf,.docx,.doc,.xlsx,.csv,.txt,.md,.rtf,.pptx,.odt,.html,.htm,.json',
  enableRoles,
  largePasteChars,
  prefill,
  showVoiceToggle = true,
}: ChatInputProps) {
  const [value, setValue] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [fileRoles, setFileRoles] = useState<('auto' | 'subject' | 'reference')[]>([])
  const [dragActive, setDragActive] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  // Use prop if provided, otherwise read from store (for backward compatibility)
  const activeConversationId = useAppStore(s => s.activeConversationId)
  const generatingConvs = useAppStore(s => s.generatingConvs)
  const isGenerating = isGeneratingProp ?? (!!activeConversationId && generatingConvs.includes(activeConversationId))

  // Voice (TTS) — the same `settings.tts.enabled` flag as the Voice panel toggle.
  const voiceOn = useAppStore(s => s.settings.tts.enabled)
  const isSpeaking = useAppStore(s => s.isSpeaking)

  const canSend = !!value.trim() || files.length > 0

  // Auto-grow: one line when empty (a long placeholder must not inflate the
  // box), then follow the content up to MAX_TEXTAREA_PX.
  useLayoutEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = `${LINE_BOX_PX}px`
    if (!value) return
    ta.style.height = `${Math.min(Math.max(ta.scrollHeight, LINE_BOX_PX), MAX_TEXTAREA_PX)}px`
  }, [value])

  // Apply an external pre-fill (example prompt chips) and focus the composer.
  useEffect(() => {
    if (!prefill) return
    setValue(prefill.text)
    const id = requestAnimationFrame(() => {
      const ta = textareaRef.current
      if (!ta) return
      ta.focus()
      const at = prefill.select ? prefill.text.indexOf(prefill.select) : -1
      if (at >= 0) ta.setSelectionRange(at, at + (prefill.select?.length ?? 0))
      else ta.setSelectionRange(prefill.text.length, prefill.text.length)
    })
    return () => cancelAnimationFrame(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill?.nonce])

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      handleSend()
    }
  }

  function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    // If largePasteChars is not set or no items in clipboard, do default paste
    if (!largePasteChars || !e.clipboardData.items) return

    let hasLargeText = false
    let textData = ''

    for (const item of e.clipboardData.items) {
      if (item.kind === 'string' && item.type === 'text/plain') {
        const text = e.clipboardData.getData('text/plain')
        if (text.length > largePasteChars) {
          hasLargeText = true
          textData = text
          break
        }
      }
    }

    if (!hasLargeText) return

    e.preventDefault()

    // Detect if it's an email (RFC822 message)
    let fileName = 'pasted-text.txt'
    let mimeType = 'text/plain'
    const emailHeaderCount = (textData.match(/^(From|To|Subject|Date):/gm) || []).length
    if (emailHeaderCount >= 2) {
      fileName = 'pasted-email.eml'
      mimeType = 'message/rfc822'
    }

    // Create File from pasted text
    const file = new File([textData], fileName, { type: mimeType })
    const newFiles = [...files, file]
    setFiles(newFiles)
    // Initialize roles to all 'auto'
    setFileRoles([...fileRoles, 'auto'])
  }

  function handleDragOver(e: React.DragEvent<HTMLDivElement>) {
    if (!enableRoles) return
    e.preventDefault()
    e.stopPropagation()
    setDragActive(true)
  }

  function handleDragLeave(e: React.DragEvent<HTMLDivElement>) {
    if (!enableRoles) return
    e.preventDefault()
    e.stopPropagation()
    // Only clear dragActive if the pointer actually left the container (not a child)
    if (e.currentTarget.contains(e.relatedTarget as Node)) return
    setDragActive(false)
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    if (!enableRoles) return
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)

    const droppedFiles = Array.from(e.dataTransfer.files || [])
    const newFiles = [...files]
    let addedCount = 0
    for (const file of droppedFiles) {
      const exists = newFiles.some(f => f.name === file.name && f.size === file.size)
      if (!exists) {
        newFiles.push(file)
        addedCount++
      }
    }
    setFiles(newFiles)
    // Initialize roles only for newly added files
    if (addedCount > 0) {
      setFileRoles([...fileRoles, ...Array(addedCount).fill('auto')])
    }
  }

  function handleSend() {
    const text = value.trim()
    if ((!text && files.length === 0) || isGenerating) return
    const opts: SendOpts = {}
    if (enableRoles && files.length > 0) {
      // Ensure roles array matches files array length
      let rolesToSend = [...fileRoles]
      if (rolesToSend.length < files.length) {
        // Pad with 'auto' if needed
        rolesToSend = [...rolesToSend, ...Array(files.length - rolesToSend.length).fill('auto')]
      } else if (rolesToSend.length > files.length) {
        // Truncate if needed
        rolesToSend = rolesToSend.slice(0, files.length)
      }
      opts.roles = rolesToSend
    }
    onSend(text, files.length > 0 ? files : undefined, opts)
    setValue('')
    setFiles([])
    setFileRoles([])
  }

  function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const selectedFiles = Array.from(e.target.files || [])
    const newFiles = [...files]
    let addedCount = 0
    for (const file of selectedFiles) {
      const exists = newFiles.some(f => f.name === file.name && f.size === file.size)
      if (!exists) {
        newFiles.push(file)
        addedCount++
      }
    }
    setFiles(newFiles)
    // Initialize roles only for newly added files
    if (addedCount > 0) {
      setFileRoles([...fileRoles, ...Array(addedCount).fill('auto')])
    }
    // Reset input so selecting the same file again works
    e.target.value = ''
  }

  function removeFile(index: number) {
    setFiles(files.filter((_, i) => i !== index))
    setFileRoles(fileRoles.filter((_, i) => i !== index))
  }

  function setFileRole(index: number, role: 'auto' | 'subject' | 'reference') {
    const newRoles = [...fileRoles]
    newRoles[index] = role
    setFileRoles(newRoles)
  }

  // Voice button: while speaking, a click stops the current speech (voice
  // stays on); otherwise it toggles voice replies on/off.
  const speakingNow = voiceOn && isSpeaking
  const voiceLabel = speakingNow
    ? 'Stop speaking'
    : voiceOn
    ? 'Voice replies on — click to mute'
    : 'Voice replies off — click to turn on'
  function handleVoiceClick() {
    if (speakingNow) stopSpeaking()
    else setVoiceEnabled(!voiceOn)
  }

  return (
    <div
      className="composer-wrap flex flex-col px-4 pt-2 pb-4"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* File chips row — shown when files are selected */}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-2 px-1">
          {files.map((file, i) => (
            <div key={i} className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full glass-card">
              <span className="text-xs text-[var(--text-primary)] max-w-[200px] truncate">{file.name}</span>
              {enableRoles && (
                <select
                  value={fileRoles[i] ?? 'auto'}
                  onChange={(e) => setFileRole(i, e.target.value as 'auto' | 'subject' | 'reference')}
                  className="text-xs glass-input rounded px-1.5 py-0.5 text-[var(--text-secondary)]"
                  title="Document classification"
                  aria-label={`Classification for ${file.name}`}
                >
                  <option value="auto">Auto</option>
                  <option value="subject">Document</option>
                  <option value="reference">Reference</option>
                </select>
              )}
              <button
                type="button"
                onClick={() => removeFile(i)}
                className="flex-shrink-0 text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
                title="Remove file"
                aria-label={`Remove ${file.name}`}
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
        accept={accept}
        multiple
      />

      {/* The composer bar: attach · text · voice · send/stop */}
      <div
        className={`composer-bar${dragActive && enableRoles ? ' is-drag' : ''}`}
        onClick={e => {
          // Clicking the bar's padding focuses the text field.
          if (e.target === e.currentTarget) textareaRef.current?.focus()
        }}
      >
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          className="composer-btn composer-ghost"
          title="Attach files or images"
          aria-label="Attach files or images"
        >
          <Paperclip className="w-[18px] h-[18px]" />
        </button>

        <textarea
          ref={textareaRef}
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder={placeholder}
          aria-label="Message"
          rows={1}
          className="composer-textarea"
        />

        <div className="composer-actions">
          {showVoiceToggle && (
            <button
              type="button"
              onClick={handleVoiceClick}
              className={`composer-btn composer-ghost${voiceOn ? ' is-on' : ''}${speakingNow ? ' is-speaking' : ''}`}
              title={voiceLabel}
              aria-label={voiceLabel}
              aria-pressed={voiceOn}
            >
              {voiceOn
                ? <Volume2 className="w-[18px] h-[18px]" />
                : <VolumeX className="w-[18px] h-[18px]" />}
            </button>
          )}

          <AnimatePresence mode="wait" initial={false}>
            {isGenerating ? (
              <motion.button
                key="stop"
                type="button"
                initial={{ scale: 0.8, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.8, opacity: 0 }}
                transition={{ duration: 0.15 }}
                onClick={onStop}
                className="composer-btn composer-stop"
                title="Stop generating"
                aria-label="Stop generating"
              >
                <Square className="w-3.5 h-3.5 fill-current" />
              </motion.button>
            ) : (
              <motion.button
                key="send"
                type="button"
                initial={{ scale: 0.8, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0.8, opacity: 0 }}
                transition={{ duration: 0.15 }}
                whileTap={canSend ? { scale: 0.92 } : undefined}
                onClick={handleSend}
                disabled={!canSend}
                className="composer-btn composer-send"
                title={canSend ? 'Send (Enter)' : 'Type a message to send'}
                aria-label="Send message"
              >
                <ArrowUp className="w-[18px] h-[18px]" strokeWidth={2.4} />
              </motion.button>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  )
}
