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
}

export function useDocChat(opts?: UseDocChatOpts): UseDocChatReturn {
  // ── Immutable refs (track in-flight state across re-renders) ──────────────
  const isMountedRef = useRef(true)
  const abortCtrlRef = useRef<AbortController | null>(null)
  const liveMessageIdRef = useRef<string | null>(null)
  const onDocsChangedRef = useRef(opts?.onDocsChanged)

  // ── State ──────────────────────────────────────────────────────────────
  const [conversations, setConversations] = useState<DocConversationSummary[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [isGenerating, setIsGenerating] = useState(false)
  const [liveTiles, setLiveTiles] = useState<Tile[]>([])
  const [selectedMessageId, setSelectedMessageId] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ── Derived state ──────────────────────────────────────────────────────

  const selectedTiles = (() => {
    if (!selectedMessageId) return []
    if (selectedMessageId === liveMessageIdRef.current) return liveTiles
    const msg = messages.find(m => m.id === selectedMessageId)
    return (msg?.meta as any)?.tiles ?? []
  })()

  // ── Effects ────────────────────────────────────────────────────────────

  // Update the refs whenever opts changes
  const onDocsUploadedRef = useRef(opts?.onDocsUploaded)
  useEffect(() => {
    onDocsChangedRef.current = opts?.onDocsChanged
    onDocsUploadedRef.current = opts?.onDocsUploaded
  }, [opts])

  useEffect(() => {
    // Re-arm on every mount: React StrictMode (dev) mounts → unmounts → mounts,
    // and a ref left at false silently drops every later state update.
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [])

  // Load conversations on mount
  useEffect(() => {
    const init = async () => {
      const convs = await listDocConversations()
      if (!isMountedRef.current) return

      setConversations(convs)

      // Open most recent or stay empty
      if (convs.length > 0) {
        const most_recent = convs[0]
        setActiveId(most_recent.id)
        const msgs = await loadDocConversation(most_recent.id)
        if (isMountedRef.current) setMessages(msgs)
      }
    }

    init()
  }, [])

  // ── Actions ────────────────────────────────────────────────────────────

  const refreshConversations = useCallback(async () => {
    const convs = await listDocConversations()
    if (isMountedRef.current) setConversations(convs)
  }, [])

  const switchConversation = useCallback(
    async (id: string) => {
      if (!isMountedRef.current) return
      setActiveId(id)
      setSelectedMessageId(null)
      setLiveTiles([])
      setError(null)

      const msgs = await loadDocConversation(id)
      if (isMountedRef.current) setMessages(msgs)
    },
    [],
  )

  const newConversation = useCallback(() => {
    if (!isMountedRef.current) return
    setActiveId(null)
    setMessages([])
    setSelectedMessageId(null)
    setLiveTiles([])
    setError(null)
  }, [])

  const deleteConversation = useCallback(
    async (id: string) => {
      if (!isMountedRef.current) return
      await deleteDocConvApi(id)
      if (!isMountedRef.current) return

      // Clean up if we were viewing it
      if (activeId === id) {
        newConversation()
      }

      // Refresh list
      await refreshConversations()
    },
    [activeId, newConversation, refreshConversations],
  )

  const selectMessage = useCallback((id: string | null) => {
    if (isMountedRef.current) setSelectedMessageId(id)
  }, [])

  const stop = useCallback(() => {
    if (abortCtrlRef.current) {
      abortCtrlRef.current.abort()
      abortCtrlRef.current = null
    }
    if (isMountedRef.current) {
      setIsGenerating(false)
      // Finalize the in-flight message
      const msgId = liveMessageIdRef.current
      if (msgId) {
        setMessages(prev => {
          const idx = prev.findIndex(m => m.id === msgId)
          if (idx === -1) return prev
          const updated = [...prev]
          updated[idx] = {
            ...updated[idx],
            isStreaming: false,
            content: updated[idx].content || 'Stopped',
          }
          return updated
        })
        liveMessageIdRef.current = null
      }
    }
  }, [])

  const send = useCallback(
    async (text: string, files?: File[], sendOpts?: DocChatSendOpts) => {
      if (isGenerating || uploading) return

      const uploadedAttachments: { doc_id: string; name: string; kind: ReturnType<typeof docKindFromName>; size: number; role: 'auto' | 'subject' | 'reference' }[] = []
      const selectedDocsArray = sendOpts?.selectedDocs ?? []

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

      // Build the message body
      const messageText = text || (files?.length ? 'Please look at the attached document(s).' : selectedDocsArray.length ? 'Please analyze the selected document(s).' : '')
      if (!messageText) return

      // Generate IDs client-side to send in request
      const userMsgId = nanoid()
      const assistantMsgId = nanoid()

      // Build full attachments list for display (uploaded + selected docs)
      const displayAttachments: Array<{ name: string; role: 'auto' | 'subject' | 'reference' }> = [
        ...uploadedAttachments.map(a => ({ name: a.name, role: a.role })),
        ...selectedDocsArray.map(sd => ({
          name: sd.name,
          role: sd.role,
        })),
      ]

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
        attachments: displayAttachments.map(a => ({
          name: a.name,
          kind: 'doc' as const,
        })),
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

      if (isMountedRef.current) {
        setMessages(prev => [...prev, userMsg, assistantMsg])
        setIsGenerating(true)
        setSelectedMessageId(assistantMsgId)
        liveMessageIdRef.current = assistantMsgId
        setLiveTiles([])
        setError(null)
      }

      // Stream the response
      abortCtrlRef.current = new AbortController()

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
      let finalTiles: Tile[] = []
      let runId = ''
      let conversationId = activeId
      let stats: Record<string, unknown> | undefined
      let lastError: string | null = null
      let finished = false

      try {

        for await (const ev of streamDocAgent(
          {
            conversation_id: activeId,
            message: messageText,
            attachments: attachmentsForRequest,
            model_override: null,
            user_message_id: userMsgId,
            assistant_message_id: assistantMsgId,
          },
          abortCtrlRef.current.signal,
        )) {
          if (!isMountedRef.current) break

          if ('error' in ev) {
            lastError = ev.error
            setError(ev.error)
            continue
          }

          if ('meta' in ev) {
            const meta = ev.meta
            runId = meta.run_id
            conversationId = meta.conversation_id
            // Update activeId after meta event
            if (isMountedRef.current) {
              setActiveId(meta.conversation_id)
            }
            continue
          }

          if ('tile' in ev) {
            const tileIdx = finalTiles.findIndex(t => t.id === ev.tile.id)
            if (tileIdx === -1) {
              finalTiles.push(ev.tile)
            } else {
              finalTiles[tileIdx] = ev.tile
            }
            if (isMountedRef.current) {
              setLiveTiles([...finalTiles])
              // Mirror into the streaming message's meta
              setMessages(prev => {
                const idx = prev.findIndex(m => m.id === assistantMsgId)
                if (idx === -1) return prev
                const updated = [...prev]
                updated[idx] = {
                  ...updated[idx],
                  meta: { ...updated[idx].meta, tiles: finalTiles },
                }
                return updated
              })
            }
            continue
          }

          if ('thinking' in ev) {
            thinkingBuffer += ev.thinking
            if (isMountedRef.current) {
              setMessages(prev => {
                const idx = prev.findIndex(m => m.id === assistantMsgId)
                if (idx === -1) return prev
                const updated = [...prev]
                updated[idx] = { ...updated[idx], thinkingContent: thinkingBuffer }
                return updated
              })
            }
            continue
          }

          if ('content' in ev) {
            contentBuffer += ev.content
            if (isMountedRef.current) {
              setMessages(prev => {
                const idx = prev.findIndex(m => m.id === assistantMsgId)
                if (idx === -1) return prev
                const updated = [...prev]
                updated[idx] = { ...updated[idx], content: contentBuffer }
                return updated
              })
            }
            continue
          }

          if ('done' in ev) {
            finished = true
            stats = ev.stats
            if (isMountedRef.current) {
              setIsGenerating(false)
              setMessages(prev => {
                const idx = prev.findIndex(m => m.id === assistantMsgId)
                if (idx === -1) return prev
                const updated = [...prev]
                const intent = typeof stats?.intent === 'string' ? stats.intent : undefined
                const docIds = uploadedAttachments.map(a => a.doc_id)
                updated[idx] = {
                  ...updated[idx],
                  isStreaming: false,
                  meta: {
                    kind: 'doc_run',
                    run_id: runId,
                    conversation_id: conversationId,
                    intent,
                    doc_ids: docIds,
                    tiles: finalTiles,
                    stats,
                    ...(lastError && { error: lastError }),
                  },
                }
                return updated
              })
            }
            liveMessageIdRef.current = null
            await refreshConversations()
          }
        }
      } catch (e) {
        if (e instanceof Error && e.name === 'AbortError') {
          // Abort was triggered by stop() which already finalized the message
          finished = true
        } else if (e instanceof Error) {
          // Non-abort error during stream: finalize and mark finished
          if (isMountedRef.current && !finished) {
            setError(e.message)
            setIsGenerating(false)
            setMessages(prev => {
              const idx = prev.findIndex(m => m.id === assistantMsgId)
              if (idx === -1) return prev
              const updated = [...prev]
              if (updated[idx].isStreaming) {
                const intent = typeof stats?.intent === 'string' ? stats.intent : undefined
                const docIds = uploadedAttachments.map(a => a.doc_id)
                updated[idx] = {
                  ...updated[idx],
                  isStreaming: false,
                  content: updated[idx].content || `⚠ ${e.message}`,
                  meta: {
                    kind: 'doc_run',
                    run_id: runId,
                    conversation_id: conversationId,
                    intent,
                    doc_ids: docIds,
                    tiles: finalTiles,
                    stats,
                    error: e.message,
                  },
                }
              }
              return updated
            })
            liveMessageIdRef.current = null
          }
          finished = true
        }
      } finally {
        abortCtrlRef.current = null

        // Finalize if stream ended without done event or error handling
        if (!finished && isMountedRef.current) {
          const liveId = assistantMsgId
          const errorMsg = lastError ?? 'The run ended unexpectedly'
          setIsGenerating(false)
          setMessages(prev => {
            const idx = prev.findIndex(m => m.id === liveId)
            if (idx === -1) return prev
            const updated = [...prev]
            if (updated[idx].isStreaming) {
              const intent = typeof stats?.intent === 'string' ? stats.intent : undefined
              const docIds = uploadedAttachments.map(a => a.doc_id)
              updated[idx] = {
                ...updated[idx],
                isStreaming: false,
                content: updated[idx].content || `⚠ ${errorMsg}`,
                meta: {
                  kind: 'doc_run',
                  run_id: runId,
                  conversation_id: conversationId,
                  intent,
                  doc_ids: docIds,
                  tiles: finalTiles,
                  stats,
                  ...(lastError && { error: lastError }),
                },
              }
            }
            return updated
          })
          liveMessageIdRef.current = null
          await refreshConversations()
        }
      }
    },
    [activeId, isGenerating, uploading, refreshConversations],
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
  }
}
