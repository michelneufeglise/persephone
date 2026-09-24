import { useState, useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { ChevronDown, ChevronRight, Play, AlertCircle, StopCircle } from 'lucide-react'
import { clsx } from 'clsx'
import type { IDPDocument } from '@/types'

interface SharedState {
  question?: string
  target?: string
  categories?: string[]
  tone?: 'natural' | 'casual' | 'professional' | 'academic'
  intensity?: 'light' | 'medium' | 'heavy'
}

interface MultiDocTabProps {
  docs: IDPDocument[]
  tab: string
  renderTab: (doc: IDPDocument, runSignal: number, onRunComplete: () => void, shared?: SharedState) => React.ReactNode
  shared?: SharedState
  onSharedChange?: {
    question?: (v: string) => void
    target?: (v: string) => void
    categories?: (v: string[]) => void
    tone?: (v: 'natural' | 'casual' | 'professional' | 'academic') => void
    intensity?: (v: 'light' | 'medium' | 'heavy') => void
  }
}

export function MultiDocTab({
  docs,
  tab,
  renderTab,
  shared,
  onSharedChange,
}: MultiDocTabProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set(docs.length > 0 ? [docs[0].id] : []))
  const [runningDocs, setRunningDocs] = useState<Set<string>>(new Set())
  const [currentDocIdx, setCurrentDocIdx] = useState<number | null>(null)
  const [runSignals, setRunSignals] = useState<Record<string, number>>({})
  const [stoppedAt, setStoppedAt] = useState<number | null>(null)
  const currentDocIdRef = useRef<string | null>(null)
  const currentSignalRef = useRef<number>(0)

  // Reset when tab or docs selection changes
  useEffect(() => {
    setRunningDocs(new Set())
    setCurrentDocIdx(null)
    setRunSignals({})
    setStoppedAt(null)
    currentDocIdRef.current = null
    currentSignalRef.current = 0
  }, [tab, docs.length])

  // Handle running the next doc in sequence
  useEffect(() => {
    if (currentDocIdx !== null) {
      const docId = docs[currentDocIdx]?.id
      if (docId && !runningDocs.has(docId)) {
        // This doc finished (or error), move to next
        const nextIdx = currentDocIdx + 1
        if (nextIdx < docs.length && stoppedAt === null) {
          setCurrentDocIdx(nextIdx)
          const nextDocId = docs[nextIdx].id
          const nextSignal = (runSignals[nextDocId] || 0) + 1
          currentDocIdRef.current = nextDocId
          currentSignalRef.current = nextSignal
          setRunSignals(prev => ({
            ...prev,
            [nextDocId]: nextSignal,
          }))
          // Auto-expand the next doc
          setExpanded(prev => new Set(prev).add(nextDocId))
        } else if (nextIdx >= docs.length) {
          // All done
          setCurrentDocIdx(null)
          currentDocIdRef.current = null
        }
      }
    }
  }, [runningDocs, currentDocIdx, docs, stoppedAt])

  const isRunning = runningDocs.size > 0
  const numCompleted = currentDocIdx !== null ? currentDocIdx : 0
  const statusText = stoppedAt !== null
    ? `Stopped at ${stoppedAt} / ${docs.length}`
    : isRunning && currentDocIdx !== null
      ? `Running ${currentDocIdx + 1} / ${docs.length}`
      : currentDocIdx !== null
        ? `Done ${numCompleted} / ${docs.length}`
        : ''
  const currentRunningFilename = currentDocIdx !== null ? docs[currentDocIdx]?.filename : null

  const handleRunAll = () => {
    if (docs.length === 0) return
    if (isRunning) return

    // Check required inputs for this tab
    if (tab === 'qa' && !shared?.question?.trim()) return
    if (tab === 'redact' && (!shared?.categories || shared.categories.length === 0)) return
    if (tab === 'translate' && !shared?.target) return
    if (tab === 'humanize' && !shared?.tone) return

    // Start with the first doc
    setCurrentDocIdx(0)
    setStoppedAt(null)
    const firstDocId = docs[0].id
    const signal = (runSignals[firstDocId] || 0) + 1
    currentDocIdRef.current = firstDocId
    currentSignalRef.current = signal
    setRunningDocs(new Set([firstDocId]))
    setRunSignals(prev => ({
      ...prev,
      [firstDocId]: signal,
    }))
    // Auto-expand the first doc
    setExpanded(prev => new Set(prev).add(firstDocId))
  }

  const handleStop = () => {
    setStoppedAt(currentDocIdx !== null ? currentDocIdx + 1 : docs.length)
  }

  const handleDocRunComplete = (docId: string) => {
    // Guard against late completions from previous runs
    if (docId !== currentDocIdRef.current) {
      return
    }
    setRunningDocs(prev => {
      const next = new Set(prev)
      next.delete(docId)
      return next
    })
  }

  // Determine which inputs are required/applicable for this tab
  const hasRunnable = !['overview', 'export'].includes(tab)
  const isQaTab = tab === 'qa'
  const isTranslateTab = tab === 'translate'
  const isRedactTab = tab === 'redact'
  const isHumanizeTab = tab === 'humanize'

  const qaAnswered = !!shared?.question?.trim()
  const translateAnswered = !!shared?.target
  const redactAnswered = !!(shared?.categories && shared.categories.length > 0)
  const humanizeAnswered = !!shared?.tone

  const canRunAll = !isRunning && (
    !isQaTab || qaAnswered
  ) && (
    !isTranslateTab || translateAnswered
  ) && (
    !isRedactTab || redactAnswered
  ) && (
    !isHumanizeTab || humanizeAnswered
  )

  return (
    <div className="flex flex-col h-full min-h-0 bg-[var(--bg-secondary)]/40">
      {/* Header bar with controls and shared inputs */}
      {hasRunnable && (
        <div className="flex-shrink-0 px-6 py-4 border-b border-[var(--border)] space-y-3 bg-[var(--bg-secondary)]/60">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <button
                onClick={handleRunAll}
                disabled={!canRunAll}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--accent)] text-white text-sm font-medium hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-colors"
              >
                <Play className="w-4 h-4" />
                Run on all {docs.length}
              </button>
              {isRunning && (
                <button
                  onClick={handleStop}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg border border-amber-500/40 bg-amber-500/10 text-amber-400 text-sm font-medium hover:bg-amber-500/20 transition-colors"
                >
                  <StopCircle className="w-4 h-4" />
                  Stop
                </button>
              )}
            </div>
            {statusText && (
              <div className="flex items-center gap-2 text-sm text-[var(--text-secondary)]">
                <div className="w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse" />
                <span>{statusText}</span>
                {currentRunningFilename && <span className="text-xs text-[var(--text-muted)]">— {currentRunningFilename}</span>}
              </div>
            )}
          </div>

          {/* Shared input controls based on tab type */}
          {isQaTab && (
            <div className="flex gap-2">
              <input
                type="text"
                value={shared?.question ?? ''}
                onChange={e => onSharedChange?.question?.(e.target.value)}
                disabled={isRunning}
                placeholder="Question for all documents…"
                className="flex-1 px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent)] disabled:opacity-50"
              />
            </div>
          )}

          {isTranslateTab && (
            <div className="flex gap-2">
              <select
                value={shared?.target ?? 'French'}
                onChange={e => onSharedChange?.target?.(e.target.value)}
                disabled={isRunning}
                className="flex-1 px-3 py-2 rounded-lg border border-[var(--border)] bg-[var(--bg-tertiary)] text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)] disabled:opacity-50"
              >
                {['French', 'German', 'Spanish', 'Italian', 'Dutch', 'Japanese', 'Chinese', 'Portuguese', 'Russian', 'Arabic', 'English'].map(l => <option key={l} value={l}>{l}</option>)}
              </select>
            </div>
          )}

          {isRedactTab && (
            <div className="flex flex-wrap gap-2">
              {['people', 'organizations', 'emails', 'phone numbers', 'addresses', 'dates', 'amounts', 'IDs', 'URLs'].map(c => (
                <button
                  key={c}
                  onClick={() => {
                    const cats = shared?.categories ?? []
                    const next = cats.includes(c) ? cats.filter(x => x !== c) : [...cats, c]
                    onSharedChange?.categories?.(next)
                  }}
                  disabled={isRunning}
                  className={clsx(
                    'px-3 py-1.5 rounded-full text-xs border transition-colors disabled:opacity-50',
                    (shared?.categories ?? []).includes(c)
                      ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                      : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
                  )}
                >
                  {c}
                </button>
              ))}
            </div>
          )}

          {isHumanizeTab && (
            <div className="space-y-2">
              <div className="flex flex-wrap gap-2">
                {(['natural', 'casual', 'professional', 'academic'] as const).map(t => (
                  <button
                    key={t}
                    onClick={() => onSharedChange?.tone?.(t)}
                    disabled={isRunning}
                    className={clsx(
                      'px-3 py-1.5 rounded-lg text-xs border transition-colors disabled:opacity-50 capitalize',
                      (shared?.tone ?? 'natural') === t
                        ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                        : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
                    )}
                  >
                    {t}
                  </button>
                ))}
              </div>
              <div className="flex flex-wrap gap-2">
                {(['light', 'medium', 'heavy'] as const).map(i => (
                  <button
                    key={i}
                    onClick={() => onSharedChange?.intensity?.(i)}
                    disabled={isRunning}
                    className={clsx(
                      'px-3 py-1.5 rounded-lg text-xs border transition-colors disabled:opacity-50 capitalize',
                      (shared?.intensity ?? 'medium') === i
                        ? 'border-[var(--accent)] bg-[var(--accent-dim)] text-[var(--accent)]'
                        : 'border-[var(--border)] bg-[var(--bg-tertiary)] text-[var(--text-secondary)] hover:border-[var(--border-bright)]',
                    )}
                  >
                    {i}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Docs list */}
      <div className="flex-1 overflow-y-auto" style={{ scrollbarWidth: 'thin' }}>
        <div className="divide-y divide-[var(--border)]">
          {docs.map((doc, idx) => {
            const docId = doc.id
            const isExpanded = expanded.has(docId)
            const isRunningDoc = runningDocs.has(docId)
            const signal = runSignals[docId] || 0
            const isDone = currentDocIdx !== null && idx < currentDocIdx
            const isCurrent = currentDocIdx === idx

            return (
              <div
                key={docId}
                className={clsx(
                  'transition-colors',
                  isCurrent && 'bg-[var(--accent-dim)]/20',
                )}
              >
                {/* Header */}
                <button
                  onClick={() => setExpanded(prev => {
                    const next = new Set(prev)
                    if (next.has(docId)) next.delete(docId)
                    else next.add(docId)
                    return next
                  })}
                  className="w-full flex items-center justify-between px-6 py-3 hover:bg-[var(--bg-tertiary)]/40 transition-colors group"
                >
                  <div className="flex items-center gap-3 flex-1 min-w-0">
                    {isExpanded ? (
                      <ChevronDown className="w-4 h-4 text-[var(--text-muted)] flex-shrink-0" />
                    ) : (
                      <ChevronRight className="w-4 h-4 text-[var(--text-muted)] flex-shrink-0" />
                    )}
                    <div className="text-left min-w-0">
                      <div className="text-sm font-medium text-[var(--text-primary)] truncate" title={doc.filename}>
                        {doc.filename}
                      </div>
                      <div className="text-xs text-[var(--text-muted)] mt-0.5">
                        {doc.pages} page{doc.pages === 1 ? '' : 's'}
                      </div>
                    </div>
                  </div>

                  {/* Status indicator */}
                  <div className="flex items-center gap-2 ml-2 flex-shrink-0">
                    {isRunningDoc && (
                      <motion.div
                        className="w-2 h-2 rounded-full bg-[var(--accent)]"
                        animate={{ opacity: [1, 0.4, 1] }}
                        transition={{ duration: 1.5, repeat: Infinity }}
                      />
                    )}
                    {isDone && (
                      <div className="text-[11px] text-[var(--text-muted)] font-mono">Done</div>
                    )}
                  </div>
                </button>

                {/* Content */}
                {isExpanded && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: 'auto' }}
                    exit={{ opacity: 0, height: 0 }}
                    transition={{ duration: 0.2 }}
                    className="border-t border-[var(--border)]"
                  >
                    <div className="px-6 py-4">
                      {renderTab(doc, signal, () => handleDocRunComplete(docId), shared)}
                    </div>
                  </motion.div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
