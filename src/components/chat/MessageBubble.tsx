import { motion } from 'framer-motion'
import { RichMarkdown } from '@/components/markdown/RichMarkdown'
import { PersephoneIcon } from '@/components/PersephoneIcon'
import { ThinkingPanel } from './ThinkingPanel'
import { ToolCallList } from './ToolCallList'
import type { Message } from '@/types'
import { Volume2, Copy, Check, Bot, ArrowUpRight, FileDown, Loader2 } from 'lucide-react'
import { useState } from 'react'

interface MessageBubbleProps {
  message: Message
  onSpeak?: (text: string) => void
  isLatest?: boolean
}

export function MessageBubble({ message, onSpeak, isLatest }: MessageBubbleProps) {
  const [copied, setCopied]       = useState(false)
  const [exportingPdf, setExportingPdf] = useState(false)
  const isUser = message.role === 'user'
  const isSystem = message.role === 'system'

  async function handleCopy() {
    await navigator.clipboard.writeText(message.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  // "Report-like" detector: assistant reply that's long enough AND has
  // structural markdown that suggests the user might want to save it as
  // a document. Kept generous — false positives (offering the button on
  // a slightly-long paragraph) are harmless.
  const isReportLike = (() => {
    if (isUser || isSystem) return false
    const c = message.content || ''
    if (c.length < 600) return false
    const hasHeading = /^#{1,4}\s/m.test(c)
    const hasList    = /^(\s*[-*+]\s|\s*\d+\.\s)/m.test(c)
    const hasTable   = /\|.+\|/.test(c)
    const hasCode    = /```/.test(c)
    // At least two "structure signals" — a bare long paragraph doesn't count.
    return [hasHeading, hasList, hasTable, hasCode].filter(Boolean).length >= 2
  })()

  async function handleExportPdf() {
    if (exportingPdf) return
    setExportingPdf(true)
    try {
      // First heading in the message becomes the title if we have one.
      const firstHeading = message.content.match(/^#{1,4}\s+(.+)$/m)?.[1]?.trim() ?? ''
      const r = await fetch('/api/chat/message/pdf', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: message.content,
          title:   firstHeading || 'Chat export',
          model:   message.model || '',
        }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const blob = await r.blob()
      const url = URL.createObjectURL(blob)
      const safe = (firstHeading || 'chat')
        .toLowerCase()
        .replace(/[^a-z0-9-]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 60)
      const a = document.createElement('a')
      a.href = url
      a.download = `${safe || 'chat'}.pdf`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch { /* silent — the browser will show a network error */ }
    finally { setExportingPdf(false) }
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
        <PersephoneIcon size={36} />
      )}

      {/* Bubble */}
      <div className={`max-w-[75%] flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
        {/* "Sent to worker" badge on user turns dispatched via the amber Bot button */}
        {isUser && (message.meta as { sent_to_worker?: boolean } | undefined)?.sent_to_worker && (
          <div className="flex items-center gap-1.5 mb-1.5 mr-2 justify-end">
            <span className="inline-flex items-center gap-1 text-[9px] font-mono px-1.5 py-0.5 rounded-full uppercase tracking-wider border border-amber-400/40 text-amber-300 bg-amber-400/10"
                  title="Sent to an auxiliary worker model — reply appears below when ready">
              <Bot className="w-2.5 h-2.5" /> sent to worker
            </span>
          </div>
        )}
        {/* Model tag + delegated badge for AI messages */}
        {!isUser && (message.model || !!message.meta?.delegated_task_id) && (
          <div className="flex items-center gap-1.5 mb-1.5 ml-2 flex-wrap">
            {message.model && (
              <span className="text-[9px] text-[var(--text-muted)] font-mono uppercase tracking-[0.25em]">
                {message.model.split(':')[0]}
              </span>
            )}
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
            {(() => {
              const meta = message.meta as
                | { delegated_task_id?: string;
                    delegated_source?: 'delegate' | 'main_model_comment';
                    delegate_model?: string;
                    delegate_category?: string } | undefined
              if (!meta?.delegated_task_id) return null
              const isMainComment = meta.delegated_source === 'main_model_comment'
              return (
                <span
                  className={`inline-flex items-center gap-1 text-[9px] font-mono px-1.5 py-0.5 rounded-full uppercase tracking-wider ${
                    isMainComment
                      ? 'border border-[var(--border)] text-[var(--text-muted)] bg-[var(--bg-tertiary)]/60'
                      : 'border border-amber-400/40 text-amber-300 bg-amber-400/10'
                  }`}
                  title={`Delegated to ${meta.delegate_model ?? '?'} (${meta.delegate_category ?? '?'})`}
                >
                  {isMainComment
                    ? <>↩ follow-up</>
                    : <><Bot className="w-2.5 h-2.5" /> delegated · {meta.delegate_model?.split(':')[0]}</>}
                </span>
              )
            })()}
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
            <>
              <SectionToc content={message.content} />
              <RichMarkdown variant="chat">{message.content}</RichMarkdown>
            </>
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
            {isReportLike && (
              <button
                onClick={handleExportPdf}
                disabled={exportingPdf}
                className="p-1.5 rounded-md text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)] transition-colors disabled:opacity-50"
                title="Export as PDF"
              >
                {exportingPdf
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <FileDown className="w-3.5 h-3.5" />}
              </button>
            )}
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


// ── Auto-TOC for extensive answers ─────────────────────────────────────────
// When the assistant reply contains 3+ H2 sections, show a compact
// section-jump strip at the top of the message. Clicking a section jumps
// (scrolls smoothly) to its heading anchor inside the bubble.
//
// Cheap heuristic — we scan the raw markdown for `^## <heading>` lines and
// build stable slug anchors. React-markdown's default heading renderer
// doesn't emit ids, but since we scroll based on text-content match we
// don't need the ids at render-time; the closest matching h2 wins.
function SectionToc({ content }: { content: string }) {
  const [collapsed, setCollapsed] = useState(false)
  const sections = extractH2Sections(content)
  if (sections.length < 3) return null

  function jumpTo(text: string) {
    // Find the closest h2 whose text contains this section's text and
    // scroll it into view. We scope to the current message bubble by
    // walking up from the toc element.
    const el = document.activeElement as HTMLElement | null
    const root = el?.closest('.max-w-\\[75\\%\\]') ?? document.body
    const headings = root.querySelectorAll('h2')
    for (const h of headings) {
      if (h.textContent && h.textContent.trim().startsWith(text.trim())) {
        h.scrollIntoView({ behavior: 'smooth', block: 'start' })
        return
      }
    }
  }

  return (
    <div className="mb-3 -mt-1 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)]/60">
      <button
        onClick={() => setCollapsed(c => !c)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-[10px] font-mono uppercase tracking-[0.24em] text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
      >
        <span>Contents · {sections.length}</span>
        <span className="ml-auto text-[9px] opacity-60">
          {collapsed ? 'show' : 'hide'}
        </span>
      </button>
      {!collapsed && (
        <div className="px-3 pb-2 flex flex-wrap gap-1.5">
          {sections.map((s, i) => (
            <button
              key={i}
              onClick={() => jumpTo(s)}
              className="text-[11px] px-2 py-0.5 rounded-full border border-[var(--border)] text-[var(--text-secondary)] hover:text-[var(--accent)] hover:border-[var(--accent)] hover:bg-[var(--accent-dim)]/40 transition-colors truncate max-w-[220px]"
              title={s}
            >
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function extractH2Sections(md: string): string[] {
  const out: string[] = []
  for (const line of (md || '').split('\n')) {
    const m = /^##\s+(.+?)\s*#*\s*$/.exec(line)
    if (m) out.push(m[1].trim())
  }
  return out
}
