import { useCallback, useState } from 'react'
import { motion } from 'framer-motion'
import { AlertCircle, X } from 'lucide-react'
import { clsx } from 'clsx'
import { ChatPane } from '@/components/chat/ChatPane'
import { SelectedDocsStrip } from './SelectedDocsStrip'
import type { Message, SendOpts } from '@/types'
import type { IDPDocument } from '@/types'
import type { UseDocChatReturn } from './useDocChat'
import { SHEET_ACCEPT } from '@/lib/docAgent'

interface DocChatProps {
  chat: UseDocChatReturn
  selectedDocIds: string[]
  selectedDocs: IDPDocument[]
  selectedDocsRoles: Record<string, 'auto' | 'subject' | 'reference'>
  onSelectedDocsRoleChange: (docId: string, role: 'auto' | 'subject' | 'reference') => void
  onDeselect: (docId: string) => void
  onClear: () => void
}

/** Example prompts: clicking one fills the composer (the template pre-selects its placeholder). */
const EXAMPLE_PROMPTS: { text: string; select?: string }[] = [
  { text: 'Who is this document about?' },
  { text: 'What is the date of birth?' },
  { text: 'Summarize this document in 3 bullets' },
  { text: 'Who is this about — and check LinkedIn if this person exists' },
  { text: 'What do we know about <name> across my documents?', select: '<name>' },
]

export function DocChat({
  chat,
  selectedDocIds,
  selectedDocs,
  selectedDocsRoles,
  onSelectedDocsRoleChange,
  onDeselect,
  onClear,
}: DocChatProps) {
  const [prefill, setPrefill] = useState<{ text: string; nonce: number; select?: string } | null>(null)

  const emptyState = (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.22, 1, 0.36, 1] }}
      className="flex flex-col items-center justify-center h-full min-h-[320px] gap-8 text-center px-8"
    >
      <div className="space-y-2">
        <div className="text-[var(--text-secondary)]">Ask about your documents</div>
        <div className="text-xs text-[var(--text-muted)] max-w-xs">
          Attach files with the paperclip, paste an email, or drop files here
        </div>
      </div>
      <div className="grid gap-2 w-full max-w-md">
        {EXAMPLE_PROMPTS.map((prompt, i) => (
          <button
            key={i}
            type="button"
            onClick={() => setPrefill({ text: prompt.text, select: prompt.select, nonce: Date.now() })}
            title={prompt.select ? 'Insert into the message box — replace the placeholder' : 'Insert into the message box'}
            className="p-3 rounded-xl glass-card glass-card-hover text-left text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)]"
          >
            “{prompt.text}”
          </button>
        ))}
      </div>
    </motion.div>
  )

  const { selectMessage, selectedMessageId, send, clearError } = chat

  const renderMessageExtra = useCallback((m: Message) => {
    // For assistant messages with tiles, show a summary
    if (m.role === 'assistant' && (m.meta as any)?.kind === 'doc_run') {
      const tiles = (m.meta as any)?.tiles || []
      const intent = (m.meta as any)?.intent
      const stopped = !!(m.meta as any)?.stopped
      if (tiles.length > 0 || intent || stopped) {
        return (
          <div className="flex items-center gap-2 flex-wrap mt-2">
            {tiles.length > 0 && (
              <button
                onClick={() => selectMessage(m.id)}
                className={clsx(
                  'text-xs px-2.5 py-1 rounded-full transition-colors font-medium flex items-center gap-1',
                  selectedMessageId === m.id
                    ? 'glass-card-active'
                    : 'glass-card glass-card-hover',
                )}
              >
                ▣ {tiles.length} step{tiles.length === 1 ? '' : 's'}
              </button>
            )}
            {intent && (
              <div className="text-xs text-[var(--text-muted)] glass-card px-2.5 py-1 rounded-full">
                intent: {intent}
              </div>
            )}
            {stopped && (
              <div className="text-xs text-amber-500 glass-card px-2.5 py-1 rounded-full" title="Generation was stopped — the answer may be incomplete">
                Stopped
              </div>
            )}
          </div>
        )
      }
    }

    // For user messages with attachments, show attachment chips
    if (m.role === 'user' && (m.meta as any)?.attachments) {
      const atts = (m.meta as any).attachments as Array<{ name: string; role: string }>
      if (atts.length > 0) {
        return (
          <div className="flex items-center gap-2 flex-wrap mt-2">
            {atts.map((att: { name: string; role: string }, i: number) => (
              <div key={i} className="text-xs text-[var(--text-muted)] glass-card px-2.5 py-1 rounded-full">
                {att.name} · {att.role === 'auto' ? 'auto' : att.role === 'subject' ? 'document' : 'reference'}
              </div>
            ))}
          </div>
        )
      }
    }

    return null
  }, [selectMessage, selectedMessageId])

  const handleSend = useCallback(
    (text: string, files?: File[], opts?: SendOpts) =>
      send(text, files, {
        ...opts,
        selectedDocs: selectedDocs.map(d => ({ doc_id: d.id, name: d.filename, role: selectedDocsRoles[d.id] ?? 'auto' })),
      }),
    [send, selectedDocs, selectedDocsRoles],
  )

  const handleSelectMessage = useCallback((m: Message) => selectMessage(m.id), [selectMessage])

  return (
    <div className="flex-1 min-h-0 flex flex-col ">
      {/* Selected docs strip */}
      <SelectedDocsStrip
        docs={selectedDocs}
        selectedDocIds={selectedDocIds}
        selectedDocsRoles={selectedDocsRoles}
        onRoleChange={onSelectedDocsRoleChange}
        onDeselect={onDeselect}
        onClear={onClear}
      />

      {/* Uploading banner */}
      {chat.uploading && (
        <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)] bg-[var(--accent-dim)] px-4 py-2">
          <motion.div
            animate={{ rotate: 360 }}
            transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }}
            className="flex-shrink-0"
          >
            <div className="w-3 h-3 rounded-full border border-[var(--accent)] border-t-transparent" />
          </motion.div>
          Uploading…
        </div>
      )}

      {/* Error banner */}
      {chat.error && (
        <div role="alert" className="flex items-center justify-between gap-2 text-xs text-red-500 bg-red-500/10 border-b border-red-500/25 px-4 py-2">
          <div className="flex items-center gap-2 min-w-0">
            <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
            <span className="break-words min-w-0">{chat.error}</span>
          </div>
          <button
            onClick={clearError}
            title="Dismiss"
            aria-label="Dismiss error"
            className="flex-shrink-0 hover:opacity-60 transition-opacity"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* ChatPane */}
      <ChatPane
        messages={chat.messages}
        isGenerating={chat.isGenerating}
        onSend={handleSend}
        onStop={chat.stop}
        enableRoles={true}
        largePasteChars={1500}
        accept={`image/*,.pdf,.docx,.doc,${SHEET_ACCEPT},.txt,.md,.rtf,.pptx,.odt,.html,.htm,.json,.xml,.eml`}
        placeholder={selectedDocIds.length > 0 ? `Ask about the ${selectedDocIds.length} selected document${selectedDocIds.length === 1 ? '' : 's'}…` : "Ask about your documents — attach files with the paperclip, paste an email, or drop files here"}
        resetKey={chat.activeId}
        selectedMessageId={selectedMessageId}
        onSelectMessage={handleSelectMessage}
        renderMessageExtra={renderMessageExtra}
        emptyState={emptyState}
        prefill={prefill}
        className="flex-1 min-h-0"
      />
    </div>
  )
}
