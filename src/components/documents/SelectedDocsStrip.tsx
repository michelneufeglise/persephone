import { useState } from 'react'
import { motion } from 'framer-motion'
import { X, ChevronDown, ChevronUp } from 'lucide-react'
import { clsx } from 'clsx'
import type { IDPDocument } from '@/types'
import { pageImageUrl } from '@/lib/idp'

interface SelectedDoc {
  doc_id: string
  role: 'auto' | 'subject' | 'reference'
}

interface SelectedDocsStripProps {
  docs: IDPDocument[]
  selectedDocIds: string[]
  selectedDocsRoles: Record<string, 'auto' | 'subject' | 'reference'>
  onRoleChange: (docId: string, role: 'auto' | 'subject' | 'reference') => void
  onDeselect: (docId: string) => void
  onClear: () => void
}

export function SelectedDocsStrip({
  docs,
  selectedDocIds,
  selectedDocsRoles,
  onRoleChange,
  onDeselect,
  onClear,
}: SelectedDocsStripProps) {
  const [expanded, setExpanded] = useState(false)

  const selectedDocs = selectedDocIds
    .map(id => docs.find(d => d.id === id))
    .filter(Boolean) as IDPDocument[]

  if (selectedDocs.length === 0) {
    return (
      <div className="px-4 py-3 border-b border-[var(--border)] bg-[var(--bg-secondary)]/60">
        <div className="text-sm text-[var(--text-muted)] text-center">
          Select documents in the library (or attach files with 📎) to ask about them
        </div>
      </div>
    )
  }

  const isCollapsed = selectedDocs.length > 4 && !expanded

  return (
    <div className="border-b border-[var(--border)] bg-[var(--bg-secondary)]/60">
      {/* Header */}
      <div className="px-4 py-3 flex items-center justify-between">
        <div className="text-sm text-[var(--text-secondary)]">
          <span className="font-medium">Selected documents ({selectedDocs.length})</span>
          <span className="text-[var(--text-muted)] ml-1.5">— the chat will work on these</span>
        </div>
        <button
          onClick={onClear}
          className="text-xs text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
        >
          Clear
        </button>
      </div>

      {/* Toggle button for many docs */}
      {selectedDocs.length > 4 && (
        <div className="px-4 pb-2">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1.5 text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition-colors"
          >
            {expanded ? (
              <>
                <ChevronUp className="w-3.5 h-3.5" />
                Collapse
              </>
            ) : (
              <>
                <ChevronDown className="w-3.5 h-3.5" />
                Show all {selectedDocs.length}
              </>
            )}
          </button>
        </div>
      )}

      {/* Docs strip */}
      <div className="overflow-x-auto px-4 pb-3" style={{ scrollbarWidth: 'thin' }}>
        <div className="flex gap-2 min-w-min">
          {(isCollapsed ? selectedDocs.slice(0, 4) : selectedDocs).map(doc => (
            <DocChip
              key={doc.id}
              doc={doc}
              role={selectedDocsRoles[doc.id] || 'auto'}
              onRoleChange={(role) => onRoleChange(doc.id, role)}
              onDeselect={() => onDeselect(doc.id)}
              isCollapsed={isCollapsed}
            />
          ))}
          {isCollapsed && selectedDocs.length > 4 && (
            <div className="flex items-center px-2 py-1 rounded-lg bg-[var(--bg-tertiary)] text-xs text-[var(--text-muted)] whitespace-nowrap">
              +{selectedDocs.length - 4} more
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function DocChip({
  doc,
  role,
  onRoleChange,
  onDeselect,
  isCollapsed,
}: {
  doc: IDPDocument
  role: 'auto' | 'subject' | 'reference'
  onRoleChange: (role: 'auto' | 'subject' | 'reference') => void
  onDeselect: () => void
  isCollapsed: boolean
}) {
  const [showRoleMenu, setShowRoleMenu] = useState(false)

  if (isCollapsed) {
    // Compact chip mode: just filename and page count
    return (
      <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-[var(--bg-tertiary)] text-xs text-[var(--text-primary)] whitespace-nowrap group relative">
        <span className="truncate max-w-[120px]">{doc.filename}</span>
        <span className="text-[var(--text-muted)]">· {doc.pages}p</span>
        <button
          onClick={onDeselect}
          className="opacity-0 group-hover:opacity-100 transition-opacity ml-0.5"
          title="Deselect"
        >
          <X className="w-3 h-3" />
        </button>
      </div>
    )
  }

  // Full card mode: thumbnail + info + role selector
  const hasImages = doc.has_images && doc.pages > 0
  const thumbnailUrl = hasImages ? pageImageUrl(doc.id, 1) : undefined

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.95 }}
      className="flex-shrink-0 w-32 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] overflow-hidden group"
    >
      {/* Thumbnail */}
      {thumbnailUrl ? (
        <div className="relative w-full h-24 bg-black/10 overflow-hidden">
          <img
            src={thumbnailUrl}
            alt={doc.filename}
            className="w-full h-full object-cover"
          />
          <button
            onClick={onDeselect}
            className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity p-1 bg-black/50 rounded hover:bg-black/70"
            title="Deselect"
          >
            <X className="w-3 h-3 text-white" />
          </button>
        </div>
      ) : (
        <div className="relative w-full h-24 bg-[var(--bg-secondary)] flex items-center justify-center">
          <div className="text-center px-2">
            <div className="text-[11px] text-[var(--text-muted)] font-mono">
              {doc.filename.split('.').pop()?.toUpperCase() || 'FILE'}
            </div>
            <div className="text-[10px] text-[var(--text-muted)] mt-1 line-clamp-2">
              {doc.preview?.slice(0, 30)}…
            </div>
          </div>
          <button
            onClick={onDeselect}
            className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity p-1 bg-black/50 rounded hover:bg-black/70"
            title="Deselect"
          >
            <X className="w-3 h-3 text-white" />
          </button>
        </div>
      )}

      {/* Info */}
      <div className="p-2 space-y-1.5">
        <div className="text-[11px] font-medium text-[var(--text-primary)] truncate" title={doc.filename}>
          {doc.filename}
        </div>
        <div className="text-[10px] text-[var(--text-muted)]">· {doc.pages}p</div>

        {/* Role selector */}
        <div className="relative">
          <button
            onClick={() => setShowRoleMenu(!showRoleMenu)}
            className={clsx(
              'w-full text-[10px] font-medium px-1.5 py-1 rounded border transition-colors',
              role === 'auto'
                ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                : 'border-[var(--border)] bg-[var(--bg-secondary)] text-[var(--text-muted)] hover:border-[var(--border-bright)]',
            )}
          >
            {role === 'auto' ? 'Auto' : role === 'subject' ? 'Document' : 'Reference'}
          </button>

          {showRoleMenu && (
            <div className="absolute top-full left-0 right-0 mt-1 bg-[var(--bg)] border border-[var(--border)] rounded-lg shadow-lg z-10 overflow-hidden">
              {(['auto', 'subject', 'reference'] as const).map(r => (
                <button
                  key={r}
                  onClick={() => {
                    onRoleChange(r)
                    setShowRoleMenu(false)
                  }}
                  className={clsx(
                    'w-full text-[10px] px-2 py-1.5 text-left transition-colors',
                    role === r
                      ? 'bg-[var(--accent-dim)] text-[var(--accent)] font-medium'
                      : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]',
                  )}
                >
                  {r === 'auto' ? 'Auto' : r === 'subject' ? 'Document' : 'Reference'}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  )
}
