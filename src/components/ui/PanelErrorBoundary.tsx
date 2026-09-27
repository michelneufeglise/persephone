import { Component, ReactNode } from 'react'
import { AlertTriangle, RotateCcw } from 'lucide-react'

interface PanelErrorBoundaryProps {
  children: ReactNode
  label?: string
  resetKey?: unknown
  framed?: boolean
}

interface PanelErrorBoundaryState {
  err: Error | null
}

export class PanelErrorBoundary extends Component<PanelErrorBoundaryProps, PanelErrorBoundaryState> {
  constructor(props: PanelErrorBoundaryProps) {
    super(props)
    this.state = { err: null }
  }

  static getDerivedStateFromError(err: Error) {
    return { err }
  }

  componentDidCatch(err: Error, info: unknown) {
    // Surface it to the browser console for the dev-tools trace too.
    // eslint-disable-next-line no-console
    console.error(`[${this.props.label ?? 'Panel'}] crashed:`, err, info)
  }

  componentDidUpdate(prevProps: PanelErrorBoundaryProps) {
    // Auto-reset when resetKey changes and error exists
    if (prevProps.resetKey !== this.props.resetKey && this.state.err) {
      this.setState({ err: null })
    }
  }

  render() {
    if (this.state.err) {
      const label = this.props.label ?? 'This panel'
      const fallback = (
        <div className="w-full h-full flex flex-col items-center justify-center gap-3 p-6 text-center">
          <AlertTriangle className="w-5 h-5 text-amber-400" />
          <div className="text-sm font-medium text-[var(--text-primary)]">
            {label} failed to render
          </div>
          <div className="text-[11px] font-mono text-[var(--text-muted)] max-w-xs line-clamp-3 break-words">
            {this.state.err.message || String(this.state.err)}
          </div>
          <button
            onClick={() => this.setState({ err: null })}
            className="pill-btn-outline flex items-center gap-1.5 hover:text-[var(--accent)]"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Retry
          </button>
        </div>
      )

      if (this.props.framed) {
        return (
          <div className="glass rounded-3xl w-full h-full">
            {fallback}
          </div>
        )
      }

      return fallback
    }

    return this.props.children
  }
}
