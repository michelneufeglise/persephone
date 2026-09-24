import { useRef, useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { MessageBubble } from './MessageBubble'
import { ChatInput } from './ChatInput'
import type { Message, SendOpts } from '@/types'
import React from 'react'

interface ChatPaneProps {
  messages: Message[]
  isGenerating: boolean
  onSend: (text: string, files?: File[], opts?: SendOpts) => void | Promise<void>
  onStop: () => void
  onSpeak?: (text: string) => void
  emptyState?: React.ReactNode
  placeholder?: string
  accept?: string
  enableRoles?: boolean
  largePasteChars?: number
  renderMessageExtra?: (m: Message) => React.ReactNode
  onSelectMessage?: (m: Message) => void
  selectedMessageId?: string | null
  className?: string
  resetKey?: string | null
}

export function ChatPane({
  messages,
  isGenerating,
  onSend,
  onStop,
  onSpeak,
  emptyState,
  placeholder,
  accept,
  enableRoles,
  largePasteChars,
  renderMessageExtra,
  onSelectMessage,
  selectedMessageId,
  className,
  resetKey,
}: ChatPaneProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollerRef = useRef<HTMLDivElement>(null)
  const [stickBottom, setStickBottom] = useState(true)

  const lastMsg = messages[messages.length - 1]

  // Compute a cheap dependency that ticks every time the tail of the
  // conversation grows — number of messages, plus the length of the last
  // message's content + thinkingContent + toolCalls. Streaming updates
  // change these but the reference to messages doesn't.
  const streamDep =
    messages.length + ':' +
    (lastMsg?.content?.length ?? 0) + ':' +
    (lastMsg?.thinkingContent?.length ?? 0) + ':' +
    (lastMsg?.toolCalls?.length ?? 0)

  useEffect(() => {
    if (!stickBottom) return
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [streamDep, stickBottom])

  // Track the user's scroll position. If they're within ~120px of the
  // bottom, we consider them "sticky"; anywhere higher and we release.
  useEffect(() => {
    const el = scrollerRef.current
    if (!el) return
    const onScroll = () => {
      const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      setStickBottom(distanceFromBottom < 120)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  // When the reset key changes (e.g., conversation switch), snap to bottom instantly.
  useEffect(() => {
    setStickBottom(true)
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [resetKey])

  const defaultEmptyState = (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      className="flex flex-col items-center justify-center h-full min-h-[320px] gap-6 text-center px-8"
    >
      <div className="text-[var(--text-secondary)]">Start a conversation</div>
    </motion.div>
  )

  return (
    <div className={`relative flex flex-col flex-1 min-h-0 ${className || ''}`}>
      {/* Messages */}
      <div
        ref={scrollerRef}
        className="relative flex-1 min-h-0 overflow-y-auto px-5 py-5 space-y-1"
        style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--scrollbar) transparent' }}
      >
        {messages.length === 0 && (emptyState ?? defaultEmptyState)}
        <AnimatePresence initial={false}>
          {messages.map(msg => (
            <MessageBubble
              key={msg.id}
              message={msg}
              onSpeak={onSpeak}
              renderExtra={renderMessageExtra}
              onSelect={onSelectMessage}
              selected={selectedMessageId === msg.id}
            />
          ))}
        </AnimatePresence>
        <div ref={bottomRef} />

        {/* "Jump to latest" pill — visible when the user has scrolled up while
            new tokens are still arriving. */}
        {!stickBottom && (isGenerating || (lastMsg?.isStreaming ?? false)) && (
          <button
            onClick={() => {
              setStickBottom(true)
              bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
            }}
            className="sticky bottom-2 left-1/2 -translate-x-1/2 flex items-center gap-1.5 px-3 py-1.5
              rounded-full text-[10px] font-mono uppercase tracking-wider text-white
              transition-all hover:scale-105 active:scale-95 z-10"
            style={{
              background: 'linear-gradient(135deg, var(--accent), var(--accent-deep))',
              boxShadow: '0 8px 22px -6px var(--accent-glow), 0 0 20px -4px var(--accent-glow)',
            }}
          >
            ↓ jump to latest
          </button>
        )}
      </div>

      <ChatInput
        onSend={onSend}
        onStop={onStop}
        isGenerating={isGenerating}
        placeholder={placeholder}
        accept={accept}
        enableRoles={enableRoles}
        largePasteChars={largePasteChars}
      />
    </div>
  )
}
