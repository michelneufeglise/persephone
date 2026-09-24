import { useState, useEffect } from 'react'
import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react'
import { clsx } from 'clsx'
import type { DocConversationSummary } from '@/lib/docAgent'

interface ConversationsListProps {
  conversations: DocConversationSummary[]
  activeId: string | null
  onSwitchConversation: (id: string) => void
  onNewConversation: () => void
  onDeleteConversation: (id: string) => Promise<void>
}

export function ConversationsList({
  conversations,
  activeId,
  onSwitchConversation,
  onNewConversation,
  onDeleteConversation,
}: ConversationsListProps) {
  const [collapsed, setCollapsed] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  // Load persisted collapsed state
  useEffect(() => {
    try {
      const saved = localStorage.getItem('persephone-docs-convs')
      if (saved) setCollapsed(JSON.parse(saved))
    } catch {}
  }, [])

  // Save collapsed state
  const toggleCollapsed = (value: boolean) => {
    setCollapsed(value)
    try {
      localStorage.setItem('persephone-docs-convs', JSON.stringify(value))
    } catch {}
  }

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirm('Delete this conversation?')) return
    setDeletingId(id)
    try {
      await onDeleteConversation(id)
    } finally {
      setDeletingId(null)
    }
  }

  if (conversations.length === 0) return null

  return (
    <div className="border-t border-[var(--border)] mt-2 pt-2">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2">
        <button
          onClick={() => toggleCollapsed(!collapsed)}
          className="flex items-center gap-1.5 flex-1 text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider hover:text-[var(--text-secondary)] transition-colors"
        >
          {collapsed ? (
            <ChevronRight className="w-3.5 h-3.5 flex-shrink-0" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5 flex-shrink-0" />
          )}
          Conversations ({conversations.length})
        </button>

        {!collapsed && (
          <button
            onClick={onNewConversation}
            title="New conversation"
            className="p-1.5 text-[var(--text-muted)] hover:text-[var(--accent)] rounded-md hover:bg-[var(--bg-tertiary)] transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* List */}
      {!collapsed && (
        <div className="space-y-1 px-2">
          {conversations.map(conv => (
            <button
              key={conv.id}
              onClick={() => onSwitchConversation(conv.id)}
              className={clsx(
                'w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-left text-xs transition-colors group',
                activeId === conv.id
                  ? 'bg-[var(--accent-dim)] text-[var(--accent)]'
                  : 'text-[var(--text-secondary)] hover:bg-[var(--bg-tertiary)]',
              )}
            >
              <div className="flex-1 min-w-0">
                <div className="font-medium truncate">{conv.title}</div>
                <div
                  className={clsx(
                    'text-[11px] mt-0.5',
                    activeId === conv.id
                      ? 'text-[var(--accent-dim)]'
                      : 'text-[var(--text-muted)]',
                  )}
                >
                  {formatRelativeTime(conv.updatedAt)}
                </div>
              </div>

              <button
                onClick={(e) => handleDelete(conv.id, e)}
                disabled={deletingId === conv.id}
                className={clsx(
                  'opacity-0 group-hover:opacity-100 p-1.5 rounded transition-all flex-shrink-0',
                  deletingId === conv.id
                    ? 'opacity-100'
                    : 'text-[var(--text-muted)] hover:text-red-400 hover:bg-red-500/10',
                )}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function formatRelativeTime(timestamp: number): string {
  const now = Date.now()
  const diffMs = now - timestamp

  const diffMins = Math.floor(diffMs / 60000)
  if (diffMins < 1) return 'now'
  if (diffMins < 60) return `${diffMins}m ago`

  const diffHours = Math.floor(diffMs / 3600000)
  if (diffHours < 24) return `${diffHours}h ago`

  const diffDays = Math.floor(diffMs / 86400000)
  if (diffDays < 7) return `${diffDays}d ago`

  const date = new Date(timestamp)
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}
