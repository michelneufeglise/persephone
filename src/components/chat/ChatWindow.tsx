import { useRef, useEffect, useCallback, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Trash2 } from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { PersephoneIcon } from '@/components/PersephoneIcon'
import { streamChat } from '@/lib/ollama'
import {
  enqueueTTS, stopTTS, extractNewSentences, extractTail,
  type SentenceCursor,
} from '@/lib/tts'
import { MessageBubble } from './MessageBubble'
import { ChatTabs } from './ChatTabs'
import { ChatInput } from './ChatInput'
import { ModelSelector } from './ModelSelector'
import { nanoid } from '@/store/nanoid'
import type { Message } from '@/types'

function syncToBackend(convId: string, conv: { title: string; model: string; updatedAt: number }, msg?: Message) {
  fetch('/api/memory/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: convId, ...conv }),
  }).catch(() => {})
  if (msg) {
    fetch(`/api/memory/conversations/${convId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(msg),
    }).catch(() => {})
  }
}

export function ChatWindow() {
  const {
    settings, getActiveConversation, activeConversationId,
    addMessage, updateMessage,
    startGenerating, stopGenerating, isConvGenerating,
    generatingConvs,
    createNewConversation, clearMessages, setIsSpeaking, setAudioLevel,
  } = useAppStore()

  // Per-conversation abort controllers. A `Map<convId, AbortController>`
  // rather than a single ref, so streams in different tabs don't clobber
  // each other's cancellation. handleStop only aborts the active tab.
  const abortMapRef = useRef<Map<string, AbortController>>(new Map())
  // Whether the currently-active tab is streaming. Computed inline from
  // `generatingConvs` (already subscribed via destructure) so re-renders
  // happen when the set changes.
  const activeIsGenerating = !!activeConversationId && generatingConvs.includes(activeConversationId)
  const bottomRef      = useRef<HTMLDivElement>(null)
  const scrollerRef    = useRef<HTMLDivElement>(null)
  // "Stick to bottom" — true while the user is at (or near) the bottom of
  // the transcript. Auto-scroll only fires when this is true; the moment
  // the user scrolls up we release, so we don't yank them back down while
  // they're reading an older part of the conversation.
  const [stickBottom, setStickBottom] = useState(true)

  const conv = getActiveConversation()
  const lastMsg = conv?.messages[conv.messages.length - 1]

  // Compute a cheap dependency that ticks every time the tail of the
  // conversation grows — number of messages, plus the length of the last
  // message's content + thinkingContent + toolCalls. Streaming updates
  // change these but the reference to conv doesn't.
  const streamDep =
    (conv?.messages.length ?? 0) + ':' +
    (lastMsg?.content?.length ?? 0)   + ':' +
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

  // When the user switches conversations, snap to bottom instantly.
  useEffect(() => {
    setStickBottom(true)
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [activeConversationId])

  useEffect(() => {
    if (!activeConversationId) createNewConversation()
  }, [])

  // Manual "Read aloud" — read latest voice/speed/volume from store at call-time.
  const handleSpeak = useCallback((text: string) => {
    const tts = useAppStore.getState().settings.tts
    if (!tts.enabled || !text.trim()) return
    stopTTS()
    setIsSpeaking(true)
    enqueueTTS(
      text,
      tts.voice,
      tts.speed,
      tts.volume,
      level => setAudioLevel(level),
      () => { setIsSpeaking(false); setAudioLevel(0) },
    )
  }, [])

  async function handleSend(text: string) {
    if (!activeConversationId) return
    const convId = activeConversationId
    // Refuse if THIS tab is already generating — but a stream in a
    // different tab is fine.
    if (isConvGenerating(convId)) return

    const userMsg: Message = {
      id: nanoid(),
      role: 'user',
      content: text,
      timestamp: Date.now(),
    }
    addMessage(convId, userMsg)

    const aiMsgId = nanoid()
    const aiMsg: Message = {
      id: aiMsgId,
      role: 'assistant',
      content: '',
      thinkingContent: '',
      timestamp: Date.now(),
      model: settings.activeModel,
      isStreaming: true,
    }
    addMessage(convId, aiMsg)
    startGenerating(convId)

    // Stop TTS from any previous turn (TTS is a single audio pipeline,
    // so we accept it being global — the active tab drives voice).
    stopTTS()

    // Per-conversation abort controller. Aborting the ACTIVE tab from
    // handleStop only cancels this stream; concurrent streams in other
    // tabs keep going.
    const controller = new AbortController()
    abortMapRef.current.set(convId, controller)

    // Streaming-TTS state
    const cursor: SentenceCursor = { pos: 0 }
    let ttsActive = false   // becomes true after first sentence is enqueued
    const onSpeakStart = () => {
      if (!ttsActive) { ttsActive = true; setIsSpeaking(true) }
    }
    const onSpeakLevel = (lvl: number) => setAudioLevel(lvl)
    const onAllSpoken  = () => { setIsSpeaking(false); setAudioLevel(0) }

    function maybeSpeakSentences(responseText: string) {
      const tts = useAppStore.getState().settings.tts
      if (!tts.enabled || !tts.autoPlay) return
      const sentences = extractNewSentences(responseText, cursor)
      for (const s of sentences) {
        onSpeakStart()
        enqueueTTS(s, tts.voice, tts.speed, tts.volume, onSpeakLevel, onAllSpoken)
      }
    }

    try {
      const currentConv = useAppStore.getState().getActiveConversation()
      const history = (currentConv?.messages ?? []).filter(
        m => m.id !== aiMsgId && !m.isStreaming,
      )

      let fullText = ''
      let thinkBuf = ''
      let responseBuf = ''
      const toolCalls: import('@/types').ToolCall[] = []

      for await (const chunk of streamChat(
        settings.activeModel,
        history,
        settings.character.systemPrompt,
        settings.model,
        controller.signal,
        settings.toolModel,
        convId,
        userMsg.id,
        settings.autoRoute,
      )) {
        if (chunk.error) break

        // MCP tool event → update toolCalls on the AI message
        if (chunk.toolEvent) {
          if (chunk.toolEvent.phase === 'start') {
            toolCalls.push({
              id:        chunk.toolEvent.id,
              name:      chunk.toolEvent.name,
              args:      chunk.toolEvent.args ?? {},
              status:    'running',
              startedAt: Date.now(),
            })
          } else {
            const idx = toolCalls.findIndex(c => c.id === chunk.toolEvent!.id)
            if (idx >= 0) {
              toolCalls[idx] = {
                ...toolCalls[idx],
                status:  chunk.toolEvent.error ? 'error' : 'done',
                preview: chunk.toolEvent.preview ?? '',
                error:   chunk.toolEvent.error,
              }
            }
          }
          updateMessage(convId, aiMsgId, { toolCalls: [...toolCalls] })
          continue
        }

        // Native / delegated thinking deltas (separate field, not <think> tags)
        if (chunk.thinking) {
          thinkBuf += chunk.thinking
          updateMessage(convId, aiMsgId, { thinkingContent: thinkBuf })
          continue
        }

        // Auto-routed: server picked a different model than `activeModel`
        if (chunk.route) {
          updateMessage(convId, aiMsgId, {
            model:        chunk.route.model,
            routedReason: chunk.route.reason,
          })
          continue
        }

        fullText += chunk.content

        // Parse <think>...</think>
        const thinkOpen  = fullText.indexOf('<think>')
        const thinkClose = fullText.indexOf('</think>')

        if (thinkOpen !== -1 && thinkClose === -1) {
          thinkBuf = fullText.slice(thinkOpen + 7)
          updateMessage(convId, aiMsgId, { thinkingContent: thinkBuf, content: '', isStreaming: true })
        } else if (thinkOpen !== -1 && thinkClose !== -1) {
          thinkBuf    = fullText.slice(thinkOpen + 7, thinkClose)
          responseBuf = fullText.slice(thinkClose + 8).trim()
          updateMessage(convId, aiMsgId, { thinkingContent: thinkBuf, content: responseBuf, isStreaming: !chunk.done })
          maybeSpeakSentences(responseBuf)
        } else {
          responseBuf = fullText
          updateMessage(convId, aiMsgId, { content: responseBuf, isStreaming: !chunk.done })
          maybeSpeakSentences(responseBuf)
        }

        if (chunk.done) {
          const finalContent = responseBuf || fullText
          const finalMsg: Partial<Message> = {
            content:         finalContent,
            thinkingContent: thinkBuf,
            isStreaming:     false,
          }
          if (chunk.stats?.tokPerSec) {
            finalMsg.tokPerSec  = chunk.stats.tokPerSec
            finalMsg.evalCount  = chunk.stats.evalCount
          }
          updateMessage(convId, aiMsgId, finalMsg)

          // Speak any trailing fragment that has no terminating punctuation
          const tts = useAppStore.getState().settings.tts
          if (tts.enabled && tts.autoPlay) {
            const tail = extractTail(finalContent, cursor)
            if (tail) {
              onSpeakStart()
              enqueueTTS(tail, tts.voice, tts.speed, tts.volume, onSpeakLevel, onAllSpoken)
            }
          }

          // Auto-title
          const fresh = useAppStore.getState().getActiveConversation()
          if (fresh?.title === 'New conversation' && fresh.messages.length >= 2) {
            const t = text.slice(0, 50) + (text.length > 50 ? '…' : '')
            useAppStore.getState().updateConversation(convId, { title: t })
          }

          // Persist to SQLite
          const updatedConv = useAppStore.getState().getActiveConversation()
          if (updatedConv) {
            syncToBackend(convId, { title: updatedConv.title, model: updatedConv.model, updatedAt: Date.now() }, userMsg)
            syncToBackend(convId, { title: updatedConv.title, model: updatedConv.model, updatedAt: Date.now() }, {
              ...aiMsg,
              content: finalContent,
              thinkingContent: thinkBuf,
              isStreaming: false,
            })
          }
          break
        }
      }
    } finally {
      stopGenerating(convId)
      abortMapRef.current.delete(convId)
    }
  }

  function handleStop() {
    // Stop only the ACTIVE tab's stream. Other tabs keep streaming.
    if (!activeConversationId) return
    const controller = abortMapRef.current.get(activeConversationId)
    controller?.abort()
    abortMapRef.current.delete(activeConversationId)
    stopGenerating(activeConversationId)
    stopTTS()
    setIsSpeaking(false)
    setAudioLevel(0)
    const c = getActiveConversation()
    if (c) {
      const last = c.messages[c.messages.length - 1]
      if (last?.isStreaming) updateMessage(c.id, last.id, { isStreaming: false })
    }
  }

  const messages = conv?.messages ?? []

  return (
    <div className="relative flex flex-col h-full glass rounded-3xl overflow-hidden">
      {/* Header */}
      <div className="relative flex items-center justify-between px-5 py-3.5 border-b border-[var(--border)] bg-[var(--bg-glass-strong)]">
        <ModelSelector />
        <div className="flex items-center gap-1">
          <button
            onClick={() => activeConversationId && clearMessages(activeConversationId)}
            className="p-2 rounded-lg text-[var(--text-muted)] hover:text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors"
            title="Clear messages in this tab"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Browser-style tabs — switch between open conversations */}
      <ChatTabs />

      {/* Messages */}
      <div
        ref={scrollerRef}
        className="relative flex-1 overflow-y-auto px-5 py-5 space-y-1"
        style={{ scrollbarWidth: 'thin', scrollbarColor: 'var(--scrollbar) transparent' }}
      >
        {messages.length === 0 && <EmptyState />}
        <AnimatePresence initial={false}>
          {messages.map(msg => (
            <MessageBubble
              key={msg.id}
              message={msg}
              onSpeak={settings.tts.enabled ? handleSpeak : undefined}
            />
          ))}
        </AnimatePresence>
        <div ref={bottomRef} />

        {/* "Jump to latest" pill — visible when the user has scrolled up while
            new tokens are still arriving. Clicking re-engages sticky-bottom. */}
        {!stickBottom && (activeIsGenerating || (lastMsg?.isStreaming ?? false)) && (
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

      <ChatInput onSend={handleSend} onStop={handleStop} />
    </div>
  )
}

function EmptyState() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      className="flex flex-col items-center justify-center h-full min-h-[320px] gap-6 text-center px-8"
    >
      {/* Persephone medallion — replaces the old holographic orb so the
          empty-chat state uses the same branding as the sidebar + wizard. */}
      <div className="animate-float">
        <PersephoneIcon size={112} />
      </div>

      <div className="space-y-2 max-w-md">
        <h2 className="font-display text-4xl text-[var(--text-primary)] leading-tight">
          Speak to <span className="font-display-italic" style={{
            background: 'linear-gradient(135deg, var(--accent), var(--holo))',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
          }}>Persephone</span>
        </h2>
        <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
          Queen of the underworld, herald of spring.
        </p>
        <p className="text-xs text-[var(--text-muted)] font-mono uppercase tracking-[0.3em] pt-2">
          what truth do you seek
        </p>
      </div>
    </motion.div>
  )
}
