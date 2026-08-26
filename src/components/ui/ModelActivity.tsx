import { useEffect, useRef, useState } from 'react'
import { clsx } from 'clsx'

function fmtElapsed(ms: number): string {
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

export function ModelActivity({
  running, phase, tokPerSec, percent, className,
}: {
  running: boolean
  phase?: string
  tokPerSec?: number
  percent?: number
  className?: string
}) {
  const [elapsed, setElapsed] = useState(0)
  const startRef = useRef<number | null>(null)

  useEffect(() => {
    if (running) {
      startRef.current = Date.now()
      setElapsed(0)
      const id = setInterval(() => {
        if (startRef.current) setElapsed(Date.now() - startRef.current)
      }, 200)
      return () => clearInterval(id)
    }
  }, [running])

  if (!running) return null
  const indeterminate = percent == null

  return (
    <div className={clsx('space-y-1.5', className)}>
      <div className="h-1.5 rounded-full bg-[var(--bg-secondary)] overflow-hidden relative">
        {indeterminate ? (
          <div
            className="absolute inset-y-0 w-1/3 bg-[var(--accent)] rounded-full"
            style={{ animation: 'indeterminate 1.2s ease-in-out infinite' }}
          />
        ) : (
          <div className="h-full bg-[var(--accent)] rounded-full transition-all" style={{ width: `${percent}%` }} />
        )}
      </div>
      <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)] font-mono">
        <span className="text-[var(--accent)] capitalize">{phase || 'working'}</span>
        <span>· {fmtElapsed(elapsed)}</span>
        {tokPerSec ? <span>· {tokPerSec} tok/s</span> : null}
      </div>
      <style>{`@keyframes indeterminate { 0% { left: -35%; } 100% { left: 100%; } }`}</style>
    </div>
  )
}
