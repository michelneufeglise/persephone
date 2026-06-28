import { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'

/**
 * Best-effort cleanup of LLM-emitted mermaid that's *almost* valid.
 * Each transform is conservative — if the source is already valid, it stays valid.
 */

// Mermaid keywords we must NEVER mistake for a bare node identifier.
const _MMD_KEYWORDS = new Set([
  'graph', 'flowchart', 'subgraph', 'end', 'direction',
  'style', 'classDef', 'class', 'click', 'linkStyle',
  'sequenceDiagram', 'classDiagram', 'stateDiagram', 'erDiagram',
  'gantt', 'pie', 'journey', 'participant', 'actor', 'note',
  'loop', 'alt', 'opt', 'par', 'critical', 'rect', 'activate', 'deactivate',
])

function sanitizeMermaid(src: string): string {
  let s = src.trim()
  // Strip accidental ```mermaid fences the model leaked into the code block
  s = s.replace(/^```(?:mermaid)?\s*\n?/i, '').replace(/\n?```\s*$/i, '')

  return s
    .split('\n')
    .map((rawLine) => {
      let line = rawLine

      // Drop trailing semicolons (mermaid doesn't need them; subgraph bodies
      // can break with them).
      line = line.replace(/;\s*$/, '')

      // Quote subgraph titles that contain spaces, slashes, commas:
      //   `subgraph My Sub`        → `subgraph "My Sub"`
      //   `subgraph Australia/NZ`  → `subgraph "Australia/NZ"`
      line = line.replace(
        /^(\s*subgraph\s+)([^"\n][^\n]*?)(\s*)$/,
        (_m, lead, title, trail) => {
          const t = title.trim()
          if (/[\s/,]/.test(t) && !/^["']/.test(t)) return `${lead}"${t}"${trail}`
          return `${lead}${t}${trail}`
        },
      )

      // Quote node LABELS inside brackets when they contain spaces:
      //   Node(Some Label)   → Node("Some Label")
      //   Node[Some Label]   → Node["Some Label"]
      //   Node((Some Label)) → Node(("Some Label"))
      //   Node[(Some Label)] → Node[("Some Label")]  (cylinder)
      line = line.replace(
        /([A-Za-z_][\w-]*)(\(\(|\[\(|\(|\[|\{)([^"'\)\]\}]*?)(\)\)|\)\]|\)|\]|\})/g,
        (full, id: string, open: string, label: string, close: string) => {
          const lbl = label.trim()
          if (!lbl) return full
          if (/[\s,./]/.test(lbl) && !/^["']/.test(lbl)) {
            return `${id}${open}"${lbl}"${close}`
          }
          return full
        },
      )

      // Bare multi-word NODE declarations (no brackets, no arrow):
      //   `        South Africa`  → `        SouthAfrica["South Africa"]`
      //   `        New Zealand`   → `        NewZealand["New Zealand"]`
      // Only fires when:
      //   - the line is whitespace-then-words (with at least one inner space),
      //   - none of those words match a mermaid keyword,
      //   - the line contains NO punctuation that would imply it's a real
      //     mermaid construct (-, =, >, <, |, ., :, (, ), [, ], {, }, ;, ,, /).
      line = line.replace(
        /^(\s*)([A-Za-z][A-Za-z0-9 _-]*?[A-Za-z])(\s*)$/,
        (m, lead, content, trail) => {
          const trimmed = content.trim()
          if (!/ /.test(trimmed)) return m                  // single word — already valid
          if (/[-=<>|.,:;()[\]{}/]/.test(trimmed)) return m // has structural chars
          const firstWord = trimmed.split(/\s+/, 1)[0].toLowerCase()
          if (_MMD_KEYWORDS.has(firstWord)) return m        // keyword line
          const id = trimmed.replace(/[^A-Za-z0-9]+/g, '_')
          return `${lead}${id}["${trimmed}"]${trail}`
        },
      )

      return line
    })
    .join('\n')
    .trim()
}

let _initialised = false
function initMermaid() {
  if (_initialised) return
  _initialised = true
  // Match the Liquid Obsidian palette via CSS vars resolved at runtime.
  const css = getComputedStyle(document.documentElement)
  const v = (name: string, fallback: string) =>
    css.getPropertyValue(name).trim() || fallback

  mermaid.initialize({
    startOnLoad:  false,
    securityLevel:'loose',          // allows custom themes
    theme:        'base',
    fontFamily:   'Manrope, system-ui, sans-serif',
    themeVariables: {
      primaryColor:        v('--accent-dim', '#d6356a22'),
      primaryTextColor:    v('--text-primary', '#f5ecff'),
      primaryBorderColor:  v('--accent', '#d6356a'),
      secondaryColor:      v('--bg-tertiary', '#141027'),
      tertiaryColor:       v('--bg-secondary', '#0c0918'),
      lineColor:           v('--accent', '#d6356a'),
      textColor:           v('--text-secondary', '#b9a4dc'),
      mainBkg:             v('--bg-tertiary', '#141027'),
      secondaryBorderColor:v('--holo', '#7df9ff'),
      tertiaryBorderColor: v('--gold', '#f0c060'),
    },
  })
}

let _counter = 0

/* ─── Error fallback ────────────────────────────────────────────────────── */
function MermaidErrorFallback({ error, source }: { error: string; source: string }) {
  const [copied, setCopied] = useState(false)

  // Extract line/column from mermaid's verbose error message if present
  const lineMatch = error.match(/line\s+(\d+)/i)
  const lineNum   = lineMatch ? parseInt(lineMatch[1], 10) : -1
  const lines     = source.split('\n')

  async function copy() {
    try {
      await navigator.clipboard.writeText(source)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {}
  }

  function openInLiveEditor() {
    // mermaid-live editor takes the source in a base64-encoded URL fragment
    const state = { code: source, mermaid: { theme: 'dark' }, autoSync: true, updateDiagram: true }
    const b64   = btoa(unescape(encodeURIComponent(JSON.stringify(state))))
    window.open(`https://mermaid.live/edit#base64:${b64}`, '_blank')
  }

  return (
    <div className="my-4 rounded-xl border border-[var(--border)] overflow-hidden"
         style={{ background: 'var(--bg-tertiary)' }}>
      {/* header bar */}
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-[var(--border)] bg-[var(--bg-glass-strong)]">
        <span className="text-[10px] font-mono uppercase tracking-[0.25em] text-amber-300/90">
          ◇ diagram source — mermaid couldn't render it
        </span>
        <div className="flex items-center gap-1">
          <button
            onClick={copy}
            className="px-2 py-1 rounded text-[10px] font-mono uppercase tracking-wider text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)] transition-colors"
            title="Copy source"
          >
            {copied ? 'copied' : 'copy'}
          </button>
          <button
            onClick={openInLiveEditor}
            className="px-2 py-1 rounded text-[10px] font-mono uppercase tracking-wider text-[var(--text-muted)] hover:text-[var(--accent)] hover:bg-[var(--accent-dim)] transition-colors"
            title="Open in mermaid.live to inspect"
          >
            ↗ live editor
          </button>
        </div>
      </div>

      {/* numbered source with offending line highlighted */}
      <pre className="text-[12px] font-mono leading-relaxed overflow-x-auto p-3 m-0">
        {lines.map((ln, i) => {
          const n = i + 1
          const isErr = n === lineNum
          return (
            <div
              key={i}
              className={`flex gap-3 ${isErr ? 'bg-red-500/15 border-l-2 border-red-500 -mx-3 px-3' : ''}`}
            >
              <span className={`select-none w-7 text-right flex-shrink-0
                ${isErr ? 'text-red-300' : 'text-[var(--text-muted)]'}`}>
                {n}
              </span>
              <span className={isErr ? 'text-red-200' : 'text-[var(--text-secondary)]'}>
                {ln || ' '}
              </span>
            </div>
          )
        })}
      </pre>

      {/* mermaid's raw message */}
      <details className="border-t border-[var(--border)]">
        <summary className="cursor-pointer text-[10px] font-mono uppercase tracking-wider text-[var(--text-muted)] px-3 py-2 hover:text-[var(--text-secondary)] select-none">
          mermaid error message
        </summary>
        <pre className="text-[11px] text-red-200/80 px-3 pb-3 overflow-auto whitespace-pre-wrap">
          {error}
        </pre>
      </details>
    </div>
  )
}

export function Mermaid({ source }: { source: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [error, setError]   = useState<string>('')
  const [didSanitize, setDidSanitize] = useState(false)

  useEffect(() => {
    if (typeof document === 'undefined') return
    initMermaid()
    let cancelled = false

    const renderInto = async (src: string) => {
      const id = `mmd-${++_counter}`
      const { svg } = await mermaid.render(id, src)
      if (cancelled || !containerRef.current) return
      containerRef.current.innerHTML = svg
    }

    const run = async () => {
      setError('')
      setDidSanitize(false)
      try {
        await renderInto(source)
      } catch (firstErr) {
        // Mermaid render mutates DOM and leaves an aborted error svg behind —
        // remove any leftover error nodes mermaid injected before retrying.
        document.querySelectorAll('[id^="dmermaid-"], .mermaid-error').forEach(el => el.remove())
        const cleaned = sanitizeMermaid(source)
        if (cleaned === source.trim()) {
          if (!cancelled) setError(String((firstErr as any)?.message ?? firstErr))
          return
        }
        try {
          await renderInto(cleaned)
          if (!cancelled) setDidSanitize(true)
        } catch (secondErr) {
          if (!cancelled) setError(String((secondErr as any)?.message ?? secondErr))
        }
      }
    }

    run()
    return () => { cancelled = true }
  }, [source])

  if (error) {
    return <MermaidErrorFallback error={error} source={source} />
  }

  return (
    <div className="my-4 relative">
      <div
        ref={containerRef}
        className="overflow-x-auto rounded-xl bg-[var(--bg-tertiary)]/60 border border-[var(--border)] p-4 flex justify-center"
        style={{ boxShadow: 'var(--shadow-soft)' }}
      />
      {didSanitize && (
        <span
          className="absolute top-1.5 right-2 text-[9px] font-mono uppercase tracking-wider text-[var(--gold)] opacity-70"
          title="Mermaid source had minor syntax issues — auto-sanitised before render."
        >
          ⊹ auto-cleaned
        </span>
      )}
    </div>
  )
}
