import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useMemo } from 'react'
import { Mermaid } from './Mermaid'
import { SketchBorder } from './SketchBorder'
import { OrnamentalDivider } from './OrnamentalDivider'
import { clsx } from 'clsx'

interface RichMarkdownProps {
  children: string
  /** Larger / fancier treatment (drop cap, dividers between H2s, etc).
   *  Use true for research reports, false for short chat replies. */
  variant?: 'chat' | 'report'
}

export function RichMarkdown({ children, variant = 'chat' }: RichMarkdownProps) {
  const components = useMemo<Components>(() => buildComponents(variant), [variant])
  return (
    <div className={clsx(
      'rich-md text-[var(--text-primary)] font-sans',
      variant === 'report' && 'rich-md--report',
    )}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  )
}

/* ─── Renderers ─────────────────────────────────────────────────────────── */
function buildComponents(variant: 'chat' | 'report'): Components {
  let h2Count = 0
  let firstParagraphDone = false

  return {
    h1: ({ children }) => (
      <h1 className="font-display text-3xl text-[var(--text-primary)] leading-tight tracking-tight mt-4 mb-3 relative pl-4"
          style={{ fontVariationSettings: "'opsz' 144" }}>
        <span className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full"
              style={{
                background: 'linear-gradient(180deg, var(--accent), var(--holo))',
                boxShadow: '0 0 10px var(--accent-glow)',
              }} />
        {children}
      </h1>
    ),

    h2: ({ children }) => {
      h2Count += 1
      const showDivider = variant === 'report' && h2Count > 1
      return (
        <>
          {showDivider && <OrnamentalDivider />}
          <h2 className="font-display text-2xl text-[var(--text-primary)] leading-tight tracking-tight mt-5 mb-3 flex items-baseline gap-3">
            <span className="inline-block w-2 h-2 rounded-full flex-shrink-0 translate-y-[-2px]"
              style={{
                background: 'radial-gradient(circle at 30% 30%, var(--holo), var(--accent))',
                boxShadow: '0 0 8px var(--accent-glow)',
              }} />
            <span>{children}</span>
          </h2>
        </>
      )
    },

    h3: ({ children }) => (
      <h3 className="font-display text-lg text-[var(--text-primary)] mt-4 mb-2 font-semibold tracking-tight">
        <span className="text-[var(--accent)] mr-2">⊹</span>
        {children}
      </h3>
    ),

    p: ({ children }) => {
      // Apply drop-cap to the first paragraph of a *report*.
      const apply = variant === 'report' && !firstParagraphDone
      firstParagraphDone = true
      return (
        <p className={clsx(
          'leading-relaxed my-2.5 text-[var(--text-primary)]',
          apply && 'rich-md__lede',
        )}>
          {children}
        </p>
      )
    },

    a: ({ href, children }) => (
      <a href={href} target="_blank" rel="noreferrer"
        className="text-[var(--accent-hover)] underline decoration-[var(--accent-dim)] decoration-2 underline-offset-2 hover:decoration-[var(--accent)] transition-colors">
        {children}
      </a>
    ),

    blockquote: ({ children }) => (
      <blockquote className="relative my-4 pl-6 pr-3 py-2 italic text-[var(--text-secondary)] font-display-italic text-[15px] leading-relaxed">
        <span aria-hidden
          className="absolute left-0 top-0 text-[44px] leading-none font-display select-none"
          style={{ color: 'var(--accent)', opacity: 0.55 }}>“</span>
        <span className="absolute left-1.5 top-2 bottom-2 w-[2px] rounded-full"
          style={{ background: 'linear-gradient(180deg, var(--accent), transparent)' }} />
        {children}
      </blockquote>
    ),

    ul: ({ children }) => (
      <ul className="my-2 space-y-1 list-none pl-2">{children}</ul>
    ),

    ol: ({ children }) => (
      <ol className="my-2 space-y-1.5 list-none pl-2 rich-md__ol counter-reset-rich">
        {children}
      </ol>
    ),

    li: ({ children, ...props }) => {
      const isOrdered = (props as { ordered?: boolean }).ordered
      // Use class-based custom counter so we don't fight react-markdown's structure
      return isOrdered ? (
        <li className="relative pl-9 leading-relaxed text-[var(--text-primary)]">
          <span className="rich-md__num" />
          {children}
        </li>
      ) : (
        <li className="relative pl-5 leading-relaxed text-[var(--text-primary)]">
          <span aria-hidden
            className="absolute left-0 top-[0.55em] w-[7px] h-[7px] rotate-45"
            style={{
              background: 'linear-gradient(135deg, var(--accent), var(--holo))',
              boxShadow: '0 0 6px var(--accent-glow)',
            }} />
          {children}
        </li>
      )
    },

    hr: () => <OrnamentalDivider />,

    code: ({ className, children, ...rest }) => {
      const isInline = !(className || '').includes('language-')
      if (isInline) {
        return (
          <code className="px-1.5 py-0.5 rounded font-mono text-[0.82em] text-[var(--accent-hover)]"
            style={{ background: 'var(--accent-dim)' }}>
            {children}
          </code>
        )
      }
      // block code — passthrough; <pre> renderer will wrap it
      return <code className={className} {...(rest as object)}>{children}</code>
    },

    pre: ({ children }) => {
      // Detect mermaid blocks — the inner <code className="language-mermaid">
      const child: any = (children as any)?.props ? children : null
      const cls = (child?.props?.className as string) || ''
      const raw = String((child?.props?.children ?? '') as string).trim()
      const lang = cls.match(/language-([\w-]+)/)?.[1] ?? ''

      if (lang === 'mermaid' && raw) {
        return <Mermaid source={raw} />
      }

      return (
        <SketchBorder
          stroke="var(--accent)"
          fillStyle="solid"
          fill="rgba(0,0,0,0.35)"
          padding={14}
          className="my-3 overflow-x-auto"
        >
          <div className="relative">
            <span className="absolute -top-1 -left-1 text-[8px] font-mono uppercase tracking-[0.28em] text-[var(--text-muted)]">
              {lang || 'code'}
            </span>
            <pre className="mt-3 text-[12.5px] leading-relaxed font-mono text-[var(--text-primary)] overflow-x-auto whitespace-pre">
              {children}
            </pre>
          </div>
        </SketchBorder>
      )
    },

    table: ({ children }) => (
      <SketchBorder
        stroke="var(--holo)"
        strokeWidth={1}
        roughness={1.2}
        padding={8}
        className="my-4 overflow-x-auto"
      >
        <table className="w-full text-[13px] border-collapse">{children}</table>
      </SketchBorder>
    ),
    thead: ({ children }) => (
      <thead className="border-b border-[var(--border-bright)]">{children}</thead>
    ),
    tr: ({ children }) => (
      <tr className="border-b border-[var(--border)] last:border-b-0">{children}</tr>
    ),
    th: ({ children }) => (
      <th className="text-left px-2 py-1.5 font-semibold text-[var(--text-primary)] text-[11px] uppercase tracking-wider">
        {children}
      </th>
    ),
    td: ({ children }) => (
      <td className="px-2 py-1.5 align-top text-[var(--text-secondary)]">{children}</td>
    ),

    strong: ({ children }) => (
      <strong className="font-semibold text-[var(--text-primary)]">{children}</strong>
    ),

    em: ({ children }) => (
      <em className="font-display-italic text-[var(--text-primary)]">{children}</em>
    ),
  }
}
