import { useCallback, useEffect, useRef, useState } from 'react'
import type { Message, SendOpts } from '@/types'
import { nanoid } from '@/store/nanoid'
import { uploadDocument } from '@/lib/idp'
import {
  streamDocAgent,
  listDocConversations,
  loadDocConversation,
  deleteDocConversation as deleteDocConvApi,
  docKindFromName,
  type Tile,
  type DocConversationSummary,
} from '@/lib/docAgent'

interface UseDocChatOpts {
  onDocsChanged?: () => void
  onDocsUploaded?: (docIds: string[]) => void
  /**
   * Fired after the user switches to a conversation (with its loaded messages) or
   * starts a new one (conversationId = null, messages = []). Not fired for the
   * initial load on mount, so a persisted library selection survives a reload.
   */
  onConversationLoaded?: (conversationId: string | null, messages: Message[]) => void
}

// Extended SendOpts for document chat (local to this module)
interface DocChatSendOpts extends SendOpts {
  selectedDocs?: Array<{ doc_id: string; name: string; role: 'auto' | 'subject' | 'reference' }>
}

export interface UseDocChatReturn {
  conversations: DocConversationSummary[]
  activeId: string | null
  messages: Message[]
  isGenerating: boolean
  liveTiles: Tile[]
  selectedMessageId: string | null
  selectedTiles: Tile[]
  uploading: boolean
  error: string | null
  send(text: string, files?: File[], sendOpts?: DocChatSendOpts): Promise<void>
  stop(): void
  selectMessage(id: string | null): void
  newConversation(): void
  switchConversation(id: string): void
  deleteConversation(id: string): Promise<void>
  refreshConversations(): Promise<void>
  clearError(): void
}

/** Latest streamed text of the in-flight assistant message (flushed once per frame). */
interface StreamBuffer {
  msgId: string
  content: string
  thinking: string
}

function patchMessage(prev: Message[], id: string, patch: (m: Message) => Message): Message[] {
  const idx = prev.findIndex(m => m.id === id)
  if (idx === -1) return prev
  const updated = [...prev]
  updated[idx] = patch(updated[idx])
  return updated
}

export function useDocChat(opts?: UseDocChatOpts): UseDocChatReturn {
  // ── Immutable refs (track in-flight state across re-renders) ──────────────
  const isMountedRef = useRef(true)
  const abortCtrlRef = useRef<AbortController | null>(null)
  const liveMessageIdRef = useRef<string | null>(null)
  const onDocsChangedRef = useRef(opts?.onDocsChanged)
  const onDocsUploadedRef = useRef(opts?.onDocsUploaded)
  const onConversationLoadedRef = useRef(opts?.onConversationLoaded)
  /** Conversation currently shown (mirrors `activeId` synchronously). */
  const activeIdRef = useRef<string | null>(null)
  /** Conversation the in-flight run belongs to (null until the server assigns one). */
  const runConvRef = useRef<string | null>(null)
  /** Sequence number of the in-flight run; 0 = no run attached to the current view. */
  const activeRunRef = useRef(0)
  const runSeqRef = useRef(0)
  /** Sequence guard for loadDocConversation (ignore out-of-order responses). */
  const loadSeqRef = useRef(0)
  /** rAF coalescing of streamed content/thinking deltas. */
  const streamBufRef = useRef<StreamBuffer | null>(null)
  const rafRef = useRef<number | null>(null)

  // ── State ──────────────────────────────────────────────────────────────
  const [conversations, setConversations] = useState<DocConversationSummary[]>([])
  const [activeId, setActiveIdState] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [isGenerating, setIsGenerating] = useState(false)
  const [liveTiles, setLiveTiles] = useState<Tile[]>([])
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const setActiveId = useCallback((id: string | null) => {
    activeIdRef.current = id
    setActiveIdState(id)
  }, [])

  // ── Derived state ──────────────────────────────────────────────────────

  const selectedTiles = (() => {
    if (!selectedMessageId) return []
    if (selectedMessageId === liveMessageIdRef.current) return liveTiles
    const msg = messages.find(m => m.id === selectedMessageId)
    return (msg?.meta as any)?.tiles ?? []
  })()

  // ── Effects ────────────────────────────────────────────────────────────

  // Keep callback refs current
  useEffect(() => {
    onDocsChangedRef.current = opts?.onDocsChanged
    onDocsUploadedRef.current = opts?.onDocsUploaded
    onConversationLoadedRef.current = opts?.onConversationLoaded
  }, [opts])

  useEffect(() => {
    // Re-arm on every mount: React StrictMode (dev) mounts → unmounts → mounts,
    // and a ref left at false silently drops every later state update.
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current)
      rafRef.current = null
      abortCtrlRef.current?.abort()
      abortCtrlRef.current = null
    }
  }, [])

  // Load conversations on mount
  useEffect(() => {
    const init = async () => {
      const convs = await listDocConversations()
      if (!isMountedRef.current) return

      setConversations(convs)

      // Open most recent — unless the user already picked/started something meanwhile.
      if (convs.length > 0 && activeIdRef.current === null && activeRunRef.current === 0) {
        const mostRecent = convs[0]
        const seq = ++loadSeqRef.current
        setActiveId(mostRecent.id)
        const msgs = await loadDocConversation(mostRecent.id)
        if (isMountedRef.current && seq === loadSeqRef.current && activeIdRef.current === mostRecent.id) {
          setMessages(msgs)
        }
      }
    }

    init()
  }, [setActiveId])

  // ── Streaming buffer helpers ───────────────────────────────────────────

  /** Apply the buffered content/thinking to the streaming message right now. */
  const flushStream = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    const buf = streamBufRef.current
    if (!buf || !isMountedRef.current) return
    setMessages(prev =>
      patchMessage(prev, buf.msgId, m =>
        m.content === buf.content && (m.thinkingContent ?? '') === buf.thinking
          ? m
          : { ...m, content: buf.content, thinkingContent: buf.thinking || m.thinkingContent },
      ),
    )
  }, [])

  const scheduleFlush = useCallback(() => {
    if (rafRef.current != null) return
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      flushStream()
    })
  }, [flushStream])

  /**
   * Detach and abort whatever run is in flight (used when the view changes).
   * The run's own loop notices it no longer belongs to the view and stops
   * touching state; the server keeps whatever it already persisted.
   */
  const detachRun = useCallback(() => {
    if (rafRef.current != null) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = null
    }
    streamBufRef.current = null
    activeRunRef.current = 0
    runConvRef.current = null
    liveMessageIdRef.current = null
    if (abortCtrlRef.current) {
      abortCtrlRef.current.abort()
      abortCtrlRef.current = null
    }
    setIsGenerating(false)
    setLiveTiles([])
  }, [])

  // ── Actions ────────────────────────────────────────────────────────────

  const refreshConversations = useCallback(async () => {
    const convs = await listDocConversations()
    if (isMountedRef.current) setConversations(convs)
  }, [])

  const switchConversation = useCallback(
    async (id: string) => {
      if (!isMountedRef.current) return
      // Already viewing it (possibly with a live run): nothing to do — don't abort or reload.
      if (id === activeIdRef.current) return
      const hadRun = activeRunRef.current !== 0
      detachRun()
      setActiveId(id)
      setSelectedMessageId(null)
      setError(null)

      const seq = ++loadSeqRef.current
      const msgs = await loadDocConversation(id)
      // Ignore stale responses (user switched again meanwhile).
      if (!isMountedRef.current || seq !== loadSeqRef.current || activeIdRef.current !== id) return
      setMessages(msgs)
      onConversationLoadedRef.current?.(id, msgs)
      if (hadRun) void refreshConversations()
    },
    [detachRun, setActiveId, refreshConversations],
  )

  const newConversation = useCallback(() => {
    if (!isMountedRef.current) return
    const hadRun = activeRunRef.current !== 0
    detachRun()
    loadSeqRef.current++ // drop any pending conversation load
    setActiveId(null)
    setMessages([])
    setSelectedMessageId(null)
    setError(null)
    onConversationLoadedRef.current?.(null, [])
    if (hadRun) void refreshConversations()
  }, [detachRun, setActiveId, refreshConversations])

  const deleteConversation = useCallback(
    async (id: string) => {
      if (!isMountedRef.current) return
      await deleteDocConvApi(id)
      if (!isMountedRef.current) return

      // Clean up if we were viewing it
      if (activeIdRef.current === id) {
        newConversation()
      }

      // Refresh list
      await refreshConversations()
    },
    [newConversation, refreshConversations],
  )

  const selectMessage = useCallback((id: string | null) => {
    if (isMountedRef.current) setSelectedMessageId(id)
  }, [])

  const clearError = useCallback(() => {
    if (isMountedRef.current) setError(null)
  }, [])

  const stop = useCallback(() => {
    // Push the last buffered tokens into the message before finalizing it.
    flushStream()
    const buf = streamBufRef.current
    streamBufRef.current = null
    activeRunRef.current = 0
    if (abortCtrlRef.current) {
      abortCtrlRef.current.abort()
      abortCtrlRef.current = null
    }
    if (isMountedRef.current) {
      setIsGenerating(false)
      // Finalize the in-flight message: keep the partial answer, mark it stopped.
      const msgId = liveMessageIdRef.current
      if (msgId) {
        setMessages(prev =>
          patchMessage(prev, msgId, m => {
            const content = (buf && buf.msgId === msgId ? buf.content : m.content) || ''
            return {
              ...m,
              isStreaming: false,
              content: content || 'Stopped',
              meta: { ...(m.meta ?? {}), stopped: true },
            }
          }),
        )
        liveMessageIdRef.current = null
      }
    }
    runConvRef.current = null
  }, [flushStream])

  const send = useCallback(
    async (text: string, files?: File[], sendOpts?: DocChatSendOpts) => {
      if (isGenerating || uploading) return

      const uploadedAttachments: { doc_id: string; name: string; kind: ReturnType<typeof docKindFromName>; size: number; role: 'auto' | 'subject' | 'reference' }[] = []
      const selectedDocsArray = sendOpts?.selectedDocs ?? []
      // The conversation this turn is sent to (captured before any await).
      const startConvId = activeIdRef.current

      // Upload files first
      if (files && files.length > 0) {
        setUploading(true)
        const uploadedDocIds: string[] = []

        try {
          for (let i = 0; i < files.length; i++) {
            const file = files[i]
            const doc = await uploadDocument(file)
            if (!doc) throw new Error(`Failed to upload ${file.name}`)
            uploadedDocIds.push(doc.id)
            const role = sendOpts?.roles?.[i] ?? 'auto'
            uploadedAttachments.push({
              doc_id: doc.id,
              name: doc.filename,
              kind: docKindFromName(doc.filename),
              size: doc.size,
              role: role as 'auto' | 'subject' | 'reference',
            })
          }
        } catch (e) {
          if (isMountedRef.current) {
            setUploading(false)
            setError(e instanceof Error ? e.message : 'Upload failed')
          }
          return
        }

        if (isMountedRef.current) {
          setUploading(false)
        }
        // Call the callbacks outside the effect, using the refs
        onDocsChangedRef.current?.()
        onDocsUploadedRef.current?.(uploadedDocIds)
      }

      // The user moved to another conversation while files were uploading: don't
      // post this turn into a conversation they're no longer looking at.
      if (!isMountedRef.current || activeIdRef.current !== startConvId) return

      // Build the message body
      const messageText = text || (files?.length ? 'Please look at the attached document(s).' : selectedDocsArray.length ? 'Please analyze the selected document(s).' : '')
      if (!messageText) return

      // Generate IDs client-side to send in request
      const userMsgId = nanoid()
      const assistantMsgId = nanoid()

      // Build full attachments list for display (uploaded + selected docs)
      const displayAttachments: Array<{ doc_id: string; name: string; role: 'auto' | 'subject' | 'reference' }> = []
      const seenDisplay = new Set<string>()
      for (const a of uploadedAttachments) {
        if (seenDisplay.has(a.doc_id)) continue
        seenDisplay.add(a.doc_id)
        displayAttachments.push({ doc_id: a.doc_id, name: a.name, role: a.role })
      }
      for (const sd of selectedDocsArray) {
        if (seenDisplay.has(sd.doc_id)) continue
        seenDisplay.add(sd.doc_id)
        displayAttachments.push({ doc_id: sd.doc_id, name: sd.name, role: sd.role })
      }

      // Append optimistic user message
      const userMsg: Message = {
        id: userMsgId,
        role: 'user',
        content: messageText,
        timestamp: Date.now(),
        meta: {
          kind: 'doc_user',
          attachments: displayAttachments,
        },
        // No top-level `attachments`: DocChat's renderMessageExtra renders the
        // chips (with their role) from meta.attachments. Setting both made
        // MessageBubble render every chip twice. Reloaded messages
        // (loadDocConversation) carry only meta, so both paths now match.
      }

      // Append optimistic assistant message (streaming placeholder)
      const assistantMsg: Message = {
        id: assistantMsgId,
        role: 'assistant',
        content: '',
        timestamp: Date.now(),
        isStreaming: true,
        meta: {
          kind: 'doc_run',
          run_id: '',
          tiles: [],
        },
      }

      // Attach this run to the current view.
      const myRun = ++runSeqRef.current
      activeRunRef.current = myRun
      runConvRef.current = startConvId
      streamBufRef.current = { msgId: assistantMsgId, content: '', thinking: '' }
      const ctrl = new AbortController()
      abortCtrlRef.current = ctrl

      /** True while this run still owns the visible conversation. */
      const belongs = () =>
        isMountedRef.current &&
        activeRunRef.current === myRun &&
        activeIdRef.current === runConvRef.current

      setMessages(prev => [...prev, userMsg, assistantMsg])
      setIsGenerating(true)
      setSelectedMessageId(assistantMsgId)
      liveMessageIdRef.current = assistantMsgId
      setLiveTiles([])
      setError(null)

      // Build attachments for the API request: combine uploaded files + selected docs, de-duplicated by doc_id
      const attachmentsByDocId = new Map<string, { doc_id: string; role: 'auto' | 'subject' | 'reference' }>()

      // Add uploaded attachments (they take precedence)
      uploadedAttachments.forEach(a => {
        attachmentsByDocId.set(a.doc_id, { doc_id: a.doc_id, role: a.role })
      })

      // Add selected docs (only if not already uploaded)
      selectedDocsArray.forEach(sd => {
        if (!attachmentsByDocId.has(sd.doc_id)) {
          attachmentsByDocId.set(sd.doc_id, { doc_id: sd.doc_id, role: sd.role })
        }
      })

      const attachmentsForRequest = Array.from(attachmentsByDocId.values())

      // Declare variables outside try block so they're accessible in catch/finally
      let thinkingBuffer = ''
      let contentBuffer = ''
      const finalTiles: Tile[] = []
      let runId = ''
      let conversationId = startConvId
      let stats: Record<string, unknown> | undefined
      let lastError: string | null = null
      let finished = false

      const finalMeta = (extra?: Record<string, unknown>) => ({
        kind: 'doc_run',
        run_id: runId,
        conversation_id: conversationId,
        intent: typeof stats?.intent === 'string' ? stats.intent : undefined,
        doc_ids: uploadedAttachments.map(a => a.doc_id),
        tiles: finalTiles,
        stats,
        ...(lastError && { error: lastError }),
        ...extra,
      })

      /** Finalize this run's assistant message (only while it still owns the view). */
      const finalizeMessage = (fallback: string | null, extra?: Record<string, unknown>) => {
        if (rafRef.current != null) {
          cancelAnimationFrame(rafRef.current)
          rafRef.current = null
        }
        if (streamBufRef.current?.msgId === assistantMsgId) streamBufRef.current = null
        setIsGenerating(false)
        setMessages(prev =>
          patchMessage(prev, assistantMsgId, m => {
            if (!m.isStreaming) return m
            return {
              ...m,
              isStreaming: false,
              content: contentBuffer || (fallback ? `⚠ ${fallback}` : m.content),
              thinkingContent: thinkingBuffer || m.thinkingContent,
              meta: finalMeta(extra),
            }
          }),
        )
        if (liveMessageIdRef.current === assistantMsgId) liveMessageIdRef.current = null
        if (activeRunRef.current === myRun) activeRunRef.current = 0
      }

      try {
        for await (const ev of streamDocAgent(
          {
            conversation_id: startConvId,
            message: messageText,
            attachments: attachmentsForRequest,
            model_override: null,
            user_message_id: userMsgId,
            assistant_message_id: assistantMsgId,
          },
          ctrl.signal,
        )) {
          // Stopped, switched away, or unmounted: stop applying events.
          if (ctrl.signal.aborted || !belongs()) break

          if ('error' in ev) {
            lastError = ev.error
            setError(ev.error)
            continue
          }

          if ('meta' in ev) {
            const meta = ev.meta
            runId = meta.run_id
            conversationId = meta.conversation_id
            // Adopt the server-assigned conversation id — only if the user is
            // still looking at the conversation this run was started from.
            runConvRef.current = meta.conversation_id
            setActiveId(meta.conversation_id)
            continue
          }

          if ('tile' in ev) {
            const tileIdx = finalTiles.findIndex(t => t.id === ev.tile.id)
            if (tileIdx === -1) {
              finalTiles.push(ev.tile)
            } else {
              finalTiles[tileIdx] = ev.tile
            }
            const snapshot = [...finalTiles]
            setLiveTiles(snapshot)
            // Mirror into the streaming message's meta
            setMessages(prev =>
              patchMessage(prev, assistantMsgId, m => ({ ...m, meta: { ...m.meta, tiles: snapshot } })),
            )
            continue
          }

          if ('thinking' in ev) {
            thinkingBuffer += ev.thinking
            if (streamBufRef.current?.msgId === assistantMsgId) streamBufRef.current.thinking = thinkingBuffer
            scheduleFlush()
            continue
          }

          if ('content' in ev) {
            contentBuffer += ev.content
            if (streamBufRef.current?.msgId === assistantMsgId) streamBufRef.current.content = contentBuffer
            scheduleFlush()
            continue
          }

          if ('done' in ev) {
            finished = true
            stats = ev.stats
            finalizeMessage(null)
            await refreshConversations()
          }
        }
      } catch (e) {
        const aborted = ctrl.signal.aborted || (e instanceof Error && e.name === 'AbortError')
        if (aborted) {
          // Stop / conversation switch already finalized the UI.
          finished = true
        } else if (e instanceof Error) {
          if (belongs() && !finished) {
            lastError = e.message
            setError(e.message)
            finalizeMessage(e.message)
          }
          finished = true
        }
      } finally {
        if (abortCtrlRef.current === ctrl) abortCtrlRef.current = null

        if (ctrl.signal.aborted) {
          // Stopped by the user (stop()) or detached by a conversation switch:
          // the UI is already consistent — just refresh the sidebar list.
          if (isMountedRef.current) void refreshConversations()
        } else if (!finished) {
          // Stream ended without a done event.
          if (belongs()) {
            finalizeMessage(lastError ?? 'The run ended unexpectedly')
          }
          if (isMountedRef.current) await refreshConversations()
        }
        if (activeRunRef.current === myRun) activeRunRef.current = 0
      }
    },
    [isGenerating, uploading, refreshConversations, scheduleFlush, setActiveId],
  )

  return {
    conversations,
    activeId,
    messages,
    isGenerating,
    liveTiles,
    selectedMessageId,
    selectedTiles,
    uploading,
    error,
    send,
    stop,
    selectMessage,
    newConversation,
    switchConversation,
    deleteConversation,
    refreshConversations,
    clearError,
  }
}
