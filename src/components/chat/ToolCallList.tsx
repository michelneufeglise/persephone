import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { Wrench, ChevronDown, CheckCircle2, AlertCircle, Bot } from 'lucide-react'
import type { ToolCall } from '@/types'
import { clsx } from 'clsx'

export function ToolCallList({ calls }: { calls: ToolCall[] }) {
  if (!calls.length) return null
  return (
    <div className="mb-2 space-y-1.5">
      {calls.map(call => <ToolCallCard key={call.id} call={call} />)}
    </div>
  )
}

function ToolCallCard({ call }: { call: ToolCall }) {
  const [open, setOpen] = useState(false)
  const [server, toolName] = call.name.split('__')
  const isDelegate = call.name === 'delegate_task'
  // Delegate calls return a JSON ack — parse it so we can show the picked
  // model + task id in the collapsed header.
  const delegateAck: { task_id?: string; delegate_model?: string; category?: string; message?: string } | null =
    isDelegate && call.status === 'done' && call.preview
      ? (() => { try { return JSON.parse(call.preview) } catch { return null } })()
      : null

  return (
    <div className={`rounded-lg border overflow-hidden ${
      isDelegate
        ? 'border-amber-400/40 bg-amber-400/5'
        : 'border-[var(--border)] bg-[var(--bg-tertiary)]'
    }`}>
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left hover:bg-[var(--bg-secondary)] transition-colors"
      >
        {isDelegate ? <Bot className="w-3.5 h-3.5 text-amber-300 shrink-0" /> : <StatusIcon status={call.status} />}

        <div className="flex-1 min-w-0">
          {isDelegate ? (
            <>
              <div className="flex items-baseline gap-1.5 flex-wrap">
                <span className="text-[11px] text-amber-300 font-mono font-medium">Delegated task</span>
                {delegateAck?.delegate_model && (
                  <>
                    <span className="text-[10px] text-[var(--text-muted)]">→</span>
                    <span className="text-[11px] text-[var(--accent)] font-mono truncate">
                      {delegateAck.delegate_model.split(':')[0]}
                    </span>
                  </>
                )}
                {delegateAck?.category && (
                  <span className="text-[9.5px] text-[var(--text-muted)] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded border border-[var(--border)]">
                    {delegateAck.category}
                  </span>
                )}
              </div>
              <span className="text-[10px] text-[var(--text-muted)] truncate block">
                {call.status === 'running' ? 'dispatching…'
                 : call.status === 'error' ? (call.error ?? 'failed')
                 : delegateAck?.message ?? 'task started — answer will appear below when ready'}
              </span>
            </>
          ) : (
            <>
              <div className="flex items-baseline gap-1.5">
                <span className="text-[11px] text-[var(--text-muted)] font-mono">{server}</span>
                <span className="text-[10px] text-[var(--text-muted)]">·</span>
                <span className="text-[11px] text-[var(--accent)] font-mono font-medium truncate">
                  {toolName}
                </span>
              </div>
              {call.status === 'running' && (
                <span className="text-[10px] text-[var(--text-muted)]">Running…</span>
              )}
              {call.status === 'done' && call.preview && (
                <span className="text-[10px] text-[var(--text-muted)] truncate block">
                  {call.preview.split('\n')[0].slice(0, 80)}
                </span>
              )}
              {call.status === 'error' && (
                <span className="text-[10px] text-red-400 truncate block">{call.error}</span>
              )}
            </>
          )}
        </div>

        <ChevronDown
          className="w-3.5 h-3.5 text-[var(--text-muted)] transition-transform"
          style={{ transform: open ? 'rotate(180deg)' : 'rotate(0deg)' }}
        />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: 'easeInOut' }}
            className="overflow-hidden"
          >
            <div className="px-2.5 pb-2 space-y-1.5 border-t border-[var(--border)] pt-2">
              <Field label="Arguments">
                <pre className="text-[10px] font-mono text-[var(--text-secondary)] whitespace-pre-wrap leading-tight">
                  {JSON.stringify(call.args, null, 2)}
                </pre>
              </Field>
              {call.preview && (
                <Field label={call.error ? 'Error' : 'Result'}>
                  <pre className={clsx(
                    'text-[10px] font-mono whitespace-pre-wrap leading-tight max-h-40 overflow-y-auto',
                    call.error ? 'text-red-300' : 'text-[var(--text-primary)]',
                  )}>
                    {call.preview}
                  </pre>
                </Field>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function StatusIcon({ status }: { status: ToolCall['status'] }) {
  if (status === 'running') {
    return (
      <motion.div
        className="w-3.5 h-3.5 rounded-full border-2 border-[var(--accent)] border-t-transparent flex-shrink-0"
        animate={{ rotate: 360 }}
        transition={{ duration: 0.8, repeat: Infinity, ease: 'linear' }}
      />
    )
  }
  if (status === 'error') {
    return <AlertCircle className="w-3.5 h-3.5 text-red-400 flex-shrink-0" />
  }
  return <Wrench className="w-3.5 h-3.5 text-green-400 flex-shrink-0" />
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[9px] text-[var(--text-muted)] uppercase tracking-wide mb-0.5">{label}</div>
      <div className="rounded bg-black/30 px-2 py-1.5">{children}</div>
    </div>
  )
}
