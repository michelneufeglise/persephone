import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
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
      <div className="px-4 py-3 border-b border-[var(--glass-stroke)] ">
        <div className="text-sm text-[var(--text-muted)] text-center">
          Select documents in the library (or attach files with 📎) to ask about them
        </div>
      </div>
    )
  }

  const isCollapsed = selectedDocs.length > 4 && !expanded

  return (
    <div className="border-b border-[var(--glass-stroke)] ">
      {/* Header */}
      <div className="px-4 py-3 flex items-center justify-between gap-2">
        <div className="text-sm text-[var(--text-secondary)] min-w-0">
          <span className="font-medium">Selected documents ({selectedDocs.length})</span>
          <span className="text-[var(--text-muted)] ml-1.5">— the chat will work on these</span>
        </div>
        <button
          onClick={onClear}
          className="flex-shrink-0 text-xs text-[var(--text-muted)] hover:text-[var(--accent)] transition-colors"
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
            <div className="flex items-center px-2 py-1 rounded-lg glass-card text-xs text-[var(--text-muted)] whitespace-nowrap">
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
  if (isCollapsed) {
    // Compact chip mode: just filename and page count
    return (
      <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg glass-card text-xs text-[var(--text-primary)] whitespace-nowrap group relative">
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
      className="flex-shrink-0 w-32 rounded-lg glass-card overflow-hidden group"
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
        <div className="relative w-full h-24 flex items-center justify-center">
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

        {/* Role selector — menu is portalled so the overflow-x-auto strip can't clip it */}
        <RoleSelect role={role} onChange={onRoleChange} />
      </div>
    </motion.div>
  )
}

type DocRole = 'auto' | 'subject' | 'reference'
const ROLES: readonly DocRole[] = ['auto', 'subject', 'reference']
const roleLabel = (r: DocRole) => (r === 'auto' ? 'Auto' : r === 'subject' ? 'Document' : 'Reference')

const MENU_GAP = 4
const MENU_EST_HEIGHT = 96 // 3 rows; refined after first layout

function RoleSelect({ role, onChange }: { role: DocRole; onChange: (r: DocRole) => void }) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ left: number; top: number; width: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const place = useCallback(() => {
    const t = triggerRef.current
    if (!t) return
    const r = t.getBoundingClientRect()
    const menuH = menuRef.current?.offsetHeight || MENU_EST_HEIGHT
    const width = Math.max(r.width, 112)
    const spaceBelow = window.innerHeight - r.bottom
    const flipUp = spaceBelow < menuH + MENU_GAP && r.top > spaceBelow
    const top = flipUp ? Math.max(MENU_GAP, r.top - menuH - MENU_GAP) : r.bottom + MENU_GAP
    const left = Math.min(Math.max(MENU_GAP, r.left), window.innerWidth - width - MENU_GAP)
    setPos({ left, top, width })
  }, [])

  const close = useCallback((refocus = false) => {
    setOpen(false)
    if (refocus) triggerRef.current?.focus()
  }, [])

  // Position before paint. The portalled menu is already mounted (hidden) in
  // this commit, so its real height is measurable here.
  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    place()
  }, [open, place])

  // Focus the selected option when the menu opens.
  useEffect(() => {
    if (!open || !pos) return
    const el = menuRef.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]')
    el?.focus()
  }, [open, pos])

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node
      if (menuRef.current?.contains(target) || triggerRef.current?.contains(target)) return
      close()
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        close(true)
      }
    }
    const onScroll = (e: Event) => {
      // Ignore scrolls inside the menu itself.
      if (menuRef.current && e.target instanceof Node && menuRef.current.contains(e.target)) return
      close()
    }
    const onResize = () => close()
    document.addEventListener('pointerdown', onPointerDown, true)
    document.addEventListener('keydown', onKey, true)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onResize)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true)
      document.removeEventListener('keydown', onKey, true)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onResize)
    }
  }, [open, close])

  function onMenuKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Home' && e.key !== 'End') return
    e.preventDefault()
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? [])
    if (items.length === 0) return
    const idx = items.indexOf(document.activeElement as HTMLButtonElement)
    let next = 0
    if (e.key === 'ArrowDown') next = idx < 0 ? 0 : (idx + 1) % items.length
    else if (e.key === 'ArrowUp') next = idx < 0 ? items.length - 1 : (idx - 1 + items.length) % items.length
    else if (e.key === 'End') next = items.length - 1
    items[next].focus()
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen(o => !o)}
        onKeyDown={e => {
          if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && !open) {
            e.preventDefault()
            setOpen(true)
          }
        }}
        className={clsx(
          'w-full text-[10px] font-medium px-1.5 py-1 rounded transition-colors',
          role === 'auto' ? 'glass-card-active' : 'glass-card glass-card-hover',
        )}
      >
        {roleLabel(role)}
      </button>

      {open &&
        createPortal(
          <div
            ref={menuRef}
            role="listbox"
            aria-label="Document role"
            onKeyDown={onMenuKeyDown}
            style={{
              position: 'fixed',
              left: pos?.left ?? -9999,
              top: pos?.top ?? -9999,
              width: pos?.width,
              visibility: pos ? 'visible' : 'hidden',
            }}
            className="glass-strong rounded-xl shadow-lg z-50 overflow-hidden py-1"
          >
            {ROLES.map(r => (
              <button
                key={r}
                type="button"
                role="option"
                aria-selected={role === r}
                onClick={() => {
                  onChange(r)
                  close(true)
                }}
                className={clsx(
                  'w-full text-[11px] px-2.5 py-1.5 text-left transition-colors focus:outline-none focus-visible:bg-[var(--glass-fill-hover)]',
                  role === r
                    ? 'bg-[var(--accent-dim)] text-[var(--accent)] font-medium'
                    : 'text-[var(--text-secondary)] hover:bg-[var(--glass-fill-hover)]',
                )}
              >
                {roleLabel(r)}
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  )
}
