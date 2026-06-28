import { motion } from 'framer-motion'
import { RichMarkdown } from '@/components/markdown/RichMarkdown'
import { ThinkingPanel } from './ThinkingPanel'
import { ToolCallList } from './ToolCallList'
import type { Message } from '@/types'
import { Volume2, Copy, Check } from 'lucide-react'
import { useState } from 'react'

interface MessageBubbleProps {
  message: Message
  onSpeak?: (text: string) => void
  isLatest?: boolean
}

export function MessageBubble({ message, onSpeak, isLatest }: MessageBubbleProps) {
  const [copied, setCopied] = useState(false)
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'

  async function handleCopy() {
    await navigator.clipboard.writeText(message.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  if (isSystem) {
    return (
      <div className="flex justify-center my-3">
        <span className="text-xs text-[var(--text-muted)] italic px-3 py-1 rounded-full bg-[var(--bg-tertiary)]">
          {message.content}
        </span>
      </div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: 'easeOut' }}
      className={`flex gap-3 group mb-4 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}
    >
      {/* Avatar */}
      {isUser ? (
        <div className="flex-shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-[10px] font-mono uppercase tracking-wider text-[var(--accent-hover)]"
          style={{
            background: 'var(--accent-dim)',
            border: '1px solid var(--border-bright)',
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.08), 0 4px 12px -6px var(--accent-glow)',
          }}
        >
          You
        </div>
      ) : (
        <div className="relative flex-shrink-0 w-9 h-9">
          <div className="absolute inset-0 rounded-full blur-md opacity-60"
            style={{ background: 'conic-gradient(from 220deg at 50% 50%, var(--orb-color-1), var(--orb-color-3), var(--orb-color-2), var(--orb-color-1))' }}
          />
          <div className="relative w-9 h-9 rounded-full flex items-center justify-center text-white text-sm font-display"
            style={{
              background: 'radial-gradient(circle at 30% 25%, rgba(255,255,255,0.4), transparent 35%), conic-gradient(from 220deg at 50% 50%, var(--orb-color-1), var(--orb-color-3), var(--orb-color-2), var(--orb-color-1))',
              boxShadow: 'inset 0 -3px 6px rgba(0,0,0,0.4), inset 0 2px 3px rgba(255,255,255,0.3), 0 0 14px var(--accent-glow)',
            }}
          >
            ⚘
          </div>
        </div>
      )}

      {/* Bubble */}
      <div className={`max-w-[75%] flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
        {/* Model tag for AI messages */}
        {!isUser && message.model && (
          <div className="flex items-center gap-1.5 mb-1.5 ml-2">
            <span className="text-[9px] text-[var(--text-muted)] font-mono uppercase tracking-[0.25em]">
              {message.model.split(':')[0]}
            </span>
            {message.routedReason && (
              <span
                className="text-[9px] font-mono px-1.5 py-0.5 rounded-full
                  bg-[var(--accent-dim)] border border-[var(--border-bright)]
                  text-[var(--accent)] tracking-wider"
                title={message.routedReason}
              >
                ⊹ {message.routedReason.replace(/^auto · /, '')}
              </span>
            )}
          </div>
        )}

        {/* Thinking panel */}
        {!isUser && message.thinkingContent && (
          <div className="w-full mb-1">
            <ThinkingPanel
              content={message.thinkingContent}
              isStreaming={message.isStreaming}
            />
          </div>
        )}

        {/* Tool calls */}
        {!isUser && message.toolCalls && message.toolCalls.length > 0 && (
          <div className="w-full">
            <ToolCallList calls={message.toolCalls} />
          </div>
        )}

        <div
          className={`relative rounded-2xl px-4 py-3 text-[14px] leading-relaxed text-[var(--text-primary)]
            ${isUser ? 'rounded-tr-md' : 'rounded-tl-md'}`}
          style={{
            background: isUser ? 'var(--user-bubble)' : 'var(--ai-bubble)',
            border: '1px solid var(--border)',
            boxShadow: 'var(--shadow-soft)',
            backdropFilter: 'blur(8px)',
            WebkitBackdropFilter: 'blur(8px)',
          }}
        >
          {message.isStreaming && !message.content ? (
            <TypingDots />
          ) : (
            <RichMarkdown variant="chat">{message.content}</RichMarkdown>
          )}
        </div>

        {/* Actions + tok/s */}
        {!message.isStreaming && message.content && (
          <div className={`flex items-center gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity ${isUser ? 'flex-row-reverse' : ''}`}>
            {!isUser && onSpeak && (
              <button
                onClick={() => onSpeak(message.content)}
                className="p-1.5 rounded-md text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)] transition-colors"
                title="Read aloud"
              >
                <Volume2 className="w-3.5 h-3.5" />
              </button>
            )}
            <button
              onClick={handleCopy}
              className="p-1.5 rounded-md text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors"
              title="Copy"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
            </button>
            <span className="text-[10px] text-[var(--text-muted)] px-1">
              {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
            {!isUser && message.tokPerSec != null && message.tokPerSec > 0 && (
              <span
                className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[var(--bg-tertiary)] ml-1"
                style={{ color: message.tokPerSec >= 30 ? 'var(--accent)' : message.tokPerSec >= 15 ? 'var(--gold)' : '#ef4444' }}
                title={`${message.evalCount} tokens generated`}
              >
                {message.tokPerSec} tok/s
              </span>
            )}
          </div>
        )}
      </div>
    </motion.div>
  )
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1 py-1">
      {[0, 1, 2].map(i => (
        <motion.span
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-[var(--accent)]"
          animate={{ opacity: [0.3, 1, 0.3] }}
          transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}
        />
      ))}
    </div>
  )
}
