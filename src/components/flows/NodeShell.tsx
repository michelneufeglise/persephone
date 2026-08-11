import { ReactNode } from 'react'
import { Handle, Position } from '@xyflow/react'
import { CheckCircle, AlertCircle, Loader2 } from 'lucide-react'
import type { FlowNodeType, NodeRunState } from '@/types/flows'
import { nodeMetadata } from './nodeMeta'

interface NodeShellProps {
  nodeId: string
  type: FlowNodeType
  children: ReactNode
  handles?: {
    target?: boolean
    source?: boolean
  }
  runState?: NodeRunState
  isSelected?: boolean
}

export function NodeShell({
  nodeId,
  type,
  children,
  handles = { target: true, source: true },
  runState,
  isSelected = false,
}: NodeShellProps) {
  const meta = nodeMetadata[type]
  const Icon = meta.icon

  const isRunning = runState?.status === 'running'
  const isDone = runState?.status === 'done'
  const isError = runState?.status === 'error'

  // Convert color to RGB for alpha blending
  const getColorWithAlpha = (color: string, alpha: number) => {
    if (color.startsWith('var(')) {
      return `rgba(214, 53, 106, ${alpha})`
    }
    // For hex colors, extract RGB
    const hex = color.replace('#', '')
    const r = parseInt(hex.substring(0, 2), 16)
    const g = parseInt(hex.substring(2, 4), 16)
    const b = parseInt(hex.substring(4, 6), 16)
    return `rgba(${r}, ${g}, ${b}, ${alpha})`
  }

  return (
    <div
      className={`
        glass rounded-2xl overflow-hidden min-w-72 shadow-lg transition-all
        ${isSelected ? 'ring-2 ring-[var(--border-bright)]' : ''}
        ${isRunning ? 'animate-pulse' : ''}
        ${isError ? 'ring-2 ring-red-500/50' : ''}
      `}
      style={{
        borderTop: `3px solid ${meta.color}`,
      }}
    >
      {/* Handles */}
      {handles.target && (
        <Handle
          type="target"
          position={Position.Left}
          className="!w-3 !h-3 !bg-current"
          style={{ background: meta.color }}
        />
      )}
      {handles.source && (
        <Handle
          type="source"
          position={Position.Right}
          className="!w-3 !h-3 !bg-current"
          style={{ background: meta.color }}
        />
      )}

      {/* Header */}
      <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5 flex-1 min-w-0">
          <div
            className="flex items-center justify-center w-6 h-6 rounded-lg flex-shrink-0"
            style={{ background: getColorWithAlpha(meta.color, 0.2) }}
          >
            <div style={{ color: meta.color }}>
              <Icon className="w-3.5 h-3.5" />
            </div>
          </div>
          <span className="text-xs font-semibold text-[var(--text-primary)]">
            {meta.label}
          </span>
        </div>

        {/* Status dot */}
        {runState && (
          <div className="flex-shrink-0">
            {isRunning && (
              <div className="relative w-2 h-2">
                <div className="absolute inset-0 bg-blue-400 rounded-full animate-pulse" />
              </div>
            )}
            {isDone && <CheckCircle className="w-4 h-4 text-green-400" />}
            {isError && <AlertCircle className="w-4 h-4 text-red-400" />}
          </div>
        )}
      </div>

      {/* Config controls */}
      <div className="px-4 py-3">{children}</div>

      {/* Run state display */}
      {runState && (
        <div className="px-4 py-3 border-t border-[var(--border)] bg-[var(--bg-secondary)] text-xs">
          {isRunning && (
            <div className="flex items-center gap-2 text-blue-400">
              <Loader2 className="w-3 h-3 animate-spin flex-shrink-0" />
              <span>Running…</span>
            </div>
          )}
          {isDone && (
            <div className="space-y-2">
              <span className="text-green-400 block font-medium">Complete</span>
              {runState.output !== undefined && (
                <div className="max-h-32 overflow-y-auto bg-[var(--bg-primary)] rounded p-2 text-[var(--text-secondary)] font-mono text-xs whitespace-pre-wrap break-words">
                  {typeof runState.output === 'string'
                    ? runState.output
                    : JSON.stringify(runState.output, null, 2)}
                </div>
              )}
            </div>
          )}
          {isError && (
            <div className="text-red-400">
              <span className="font-medium block">Error</span>
              <span className="text-red-300 text-xs mt-1">{runState.error}</span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
