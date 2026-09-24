import { useRef } from 'react'
import { motion } from 'framer-motion'
import { AlertCircle, X } from 'lucide-react'
import { clsx } from 'clsx'
import { ChatPane } from '@/components/chat/ChatPane'
import { SelectedDocsStrip } from './SelectedDocsStrip'
import type { Message } from '@/types'
import type { IDPDocument } from '@/types'
import type { UseDocChatReturn } from './useDocChat'

interface DocChatProps {
  chat: UseDocChatReturn
  selectedDocIds: string[]
  selectedDocs: IDPDocument[]
  selectedDocsRoles: Record<string, 'auto' | 'subject' | 'reference'>
  onSelectedDocsRoleChange: (docId: string, role: 'auto' | 'subject' | 'reference') => void
  onDeselect: (docId: string) => void
  onClear: () => void
}

const EXAMPLE_PROMPTS = [
  'Verify the signature on contract.pdf against the reference card',
  'Who is this document about?',
  'Summarize this email',
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
          <div
            key={i}
            className="p-3 rounded-lg border border-[var(--border)] text-left text-xs text-[var(--text-muted)] bg-[var(--bg-tertiary)]/30"
          >
            "{prompt}"
          </div>
        ))}
      </div>
    </motion.div>
  )

  const renderMessageExtra = (m: Message) => {
    // For assistant messages with tiles, show a summary
    if (m.role === 'assistant' && (m.meta as any)?.kind === 'doc_run') {
      const tiles = (m.meta as any)?.tiles || []
      const intent = (m.meta as any)?.intent
      if (tiles.length > 0 || intent) {
        return (
          <div className="flex items-center gap-2 flex-wrap mt-2">
            {tiles.length > 0 && (
              <button
                onClick={() => chat.selectMessage(m.id)}
                className={clsx(
                  'text-xs px-2.5 py-1 rounded-full transition-colors font-medium flex items-center gap-1',
                  chat.selectedMessageId === m.id
                    ? 'bg-[var(--accent-dim)] text-[var(--accent)]'
                    : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:bg-[var(--border)]',
                )}
              >
                ▣ {tiles.length} step{tiles.length === 1 ? '' : 's'}
              </button>
            )}
            {intent && (
              <div className="text-xs text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2.5 py-1 rounded-full">
                intent: {intent}
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
              <div key={i} className="text-xs text-[var(--text-muted)] bg-[var(--bg-tertiary)] px-2.5 py-1 rounded-full">
                {att.name} · {att.role === 'auto' ? 'auto' : att.role === 'subject' ? 'document' : 'reference'}
              </div>
            ))}
          </div>
        )
      }
    }

    return null
  }

  return (
    <div className="h-full flex flex-col bg-[var(--bg-secondary)]/40">
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
        <div className="flex items-center justify-between gap-2 text-xs text-red-500 bg-red-500/10 border-b border-red-500/25 px-4 py-2">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
            {chat.error}
          </div>
          <button
            onClick={() => {
              // Error clears when user tries again
            }}
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
        onSend={(text, files, opts) => chat.send(text, files, { ...opts, selectedDocs: selectedDocs.map(d => ({ doc_id: d.id, name: d.filename, role: selectedDocsRoles[d.id] ?? 'auto' })) })}
        onStop={chat.stop}
        enableRoles={true}
        largePasteChars={1500}
        accept="image/*,.pdf,.docx,.doc,.xlsx,.csv,.txt,.md,.rtf,.pptx,.odt,.html,.htm,.json,.xml,.eml"
        placeholder={selectedDocIds.length > 0 ? `Ask about the ${selectedDocIds.length} selected document${selectedDocIds.length === 1 ? '' : 's'}…` : "Ask about your documents — attach files with the paperclip, paste an email, or drop files here"}
        resetKey={chat.activeId}
        selectedMessageId={chat.selectedMessageId}
        onSelectMessage={m => chat.selectMessage(m.id)}
        renderMessageExtra={renderMessageExtra}
        emptyState={emptyState}
        className="flex-1 min-h-0"
      />
    </div>
  )
}
