import { useRef, useEffect, useCallback, useState } from 'react'
import { motion } from 'framer-motion'
import { Trash2 } from 'lucide-react'
import { useAppStore } from '@/store/appStore'
import { PersephoneIcon } from '@/components/PersephoneIcon'
import { streamChat } from '@/lib/ollama'
import { uploadDocument } from '@/lib/idp'
import {
  enqueueTTS, stopTTS, extractNewSentences, extractTail,
  type SentenceCursor,
} from '@/lib/tts'
import { ChatPane } from './ChatPane'
import { ChatTabs } from './ChatTabs'
import { ModelSelector } from './ModelSelector'
import { nanoid } from '@/store/nanoid'
import type { Message, SendOpts } from '@/types'

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

  const conv = getActiveConversation()

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

  async function handleSend(text: string, files?: File[], opts?: SendOpts) {
    if (!activeConversationId) return
    const convId = activeConversationId
    // Refuse if THIS tab is already generating — but a stream in a
    // different tab is fine.
    if (isConvGenerating(convId)) return

    // Process file attachments
    const attachments: NonNullable<Message['attachments']> = []
    const images: string[] = []
    let attachedText = ''

    if (files && files.length > 0) {
      for (const file of files) {
        try {
          if (file.type.startsWith('image/')) {
            // Read image as data URL
            const dataUrl = await new Promise<string>((res, rej) => {
              const r = new FileReader()
              r.onload = () => res(String(r.result))
              r.onerror = rej
              r.readAsDataURL(file)
            })
            // Strip data:...;base64, prefix
            const base64 = dataUrl.split(',')[1]
            images.push(base64)
            attachments.push({ name: file.name, kind: 'image', preview: dataUrl })
          } else {
            // Upload document via IDP
            const doc = await uploadDocument(file)
            if (doc) {
              attachedText += `\n\n[Attached document: ${file.name}]\n${(doc.text || '').substring(0, 20000)}\n`
              attachments.push({ name: file.name, kind: 'doc' })
            }
          }
        } catch (err) {
          console.error(`Failed to process file ${file.name}:`, err)
        }
      }
    }

    const finalUserContent = text + attachedText

    const userMsg: Message = {
      id: nanoid(),
      role: 'user',
      content: finalUserContent,
      timestamp: Date.now(),
      attachments: attachments.length ? attachments : undefined,
    }
    // Attach images to the message for backend processing
    if (images.length) {
      (userMsg as any).images = images
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

      {/* Chat pane — message list + composer */}
      <ChatPane
        messages={messages}
        isGenerating={activeIsGenerating}
        onSend={handleSend}
        onStop={handleStop}
        onSpeak={settings.tts.enabled ? handleSpeak : undefined}
        emptyState={<EmptyState />}
        resetKey={activeConversationId}
      />
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
