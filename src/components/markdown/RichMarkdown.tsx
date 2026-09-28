import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useMemo, useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { Mermaid } from './Mermaid'
import { OrnamentalDivider } from './OrnamentalDivider'
import { withInlineIcons } from './InlineIcons'
import { remarkAnswerStructure } from './remarkAnswerStructure'
import { clsx } from 'clsx'
import './richMarkdown.css'

// Long file names (e.g. DEMO_company_registry_extract_….pdf) have no spaces,
// so a table column / inline code cannot shrink below them: offer line-break
// opportunities after _ . / - via <wbr> (not copied with the text, unlike a
// zero-width space), so short words like RESULT never split.
function softBreakText(text: string, keyBase: string): React.ReactNode {
  if (text.length < 16 || !/[_./-]\S/.test(text)) return text
  const parts = text.split(/(?<=[_./-])(?=\S)/)
  if (parts.length < 2) return text
  const out: React.ReactNode[] = []
  parts.forEach((p, i) => {
    if (i > 0) out.push(<wbr key={`${keyBase}-w${i}`} />)
    out.push(p)
  })
  return out
}
function softBreakChildren(children: React.ReactNode): React.ReactNode {
  const soft = (c: React.ReactNode, i: number) =>
    typeof c === 'string' ? softBreakText(c, `sb${i}`) : c
  return Array.isArray(children) ? children.map(soft) : soft(children, 0)
}

interface RichMarkdownProps {
  children: string
  /** 'report' = research reports (display-serif title, ornamental dividers
   *  between H2 sections); 'chat' = assistant answers. */
  variant?: 'chat' | 'report'
}

export function RichMarkdown({ children, variant = 'chat' }: RichMarkdownProps) {
  const components = useMemo<Components>(() => buildComponents(variant), [variant])
  const remarkPlugins = useMemo(
    () => [remarkGfm, [remarkAnswerStructure, { variant }]] as NonNullable<React.ComponentProps<typeof ReactMarkdown>['remarkPlugins']>,
    [variant],
  )
  return (
    <div className={clsx('rich-md', variant === 'report' ? 'rich-md--report' : 'rich-md--chat')}>
      <ReactMarkdown remarkPlugins={remarkPlugins} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  )
}

/* ─── Status cells (✅ / ⚠️ / ❌) ──────────────────────────────────────── */
type Status = 'ok' | 'warn' | 'bad'
const STATUS_GLYPHS: Array<[Status, RegExp]> = [
  ['ok',   /✅|✔|✓|☑|🟢/u],
  ['warn', /⚠|🟡|🟠/u],
  ['bad',  /❌|✗|✘|❎|🔴|⛔|🚫/u],
]
const LEADING_GLYPH_RE = /^(\s*)(✅|✔️?|✓|☑️?|🟢|⚠️?|🟡|🟠|❌|✗|✘|❎|🔴|⛔|🚫)\s*/u

interface HastLike { type?: string; value?: string; children?: HastLike[] }
function hastText(node: HastLike | undefined, depth = 0): string {
  if (!node || depth > 20) return ''
  if (node.type === 'text') return node.value ?? ''
  return Array.isArray(node.children) ? node.children.map(c => hastText(c, depth + 1)).join('') : ''
}

function statusOf(text: string): Status | null {
  let best: Status | null = null
  let bestAt = Infinity
  for (const [status, re] of STATUS_GLYPHS) {
    const m = re.exec(text)
    if (m && m.index < bestAt) { best = status; bestAt = m.index }
  }
  return best
}

/** Wrap a leading status emoji in a fixed-width span so labels line up. */
function alignLeadingGlyph(children: React.ReactNode): React.ReactNode {
  const list = Array.isArray(children) ? children : [children]
  const first = list[0]
  if (typeof first !== 'string') return children
  const m = LEADING_GLYPH_RE.exec(first)
  if (!m) return children
  return [
    <span key="st-icon" className="rmd-status__icon">{m[2]}</span>,
    first.slice(m[0].length),
    ...list.slice(1),
  ]
}

/* ─── Renderers ─────────────────────────────────────────────────────────── */
function buildComponents(variant: 'chat' | 'report'): Components {
  const isReport = variant === 'report'

  return {
    h1: ({ children, className }) => isReport ? (
      // Research reports keep the display-serif title.
      <h1 className={clsx('rmd-h1 rmd-h1--display font-display', className)}
          style={{ fontVariationSettings: "'opsz' 144" }}>
        <span className="rmd-h1__bar" aria-hidden />
        {withInlineIcons(children)}
      </h1>
    ) : (
      <h1 className={clsx('rmd-h1', className)}>{withInlineIcons(children)}</h1>
    ),

    h2: ({ children, className }) => (
      <h2 className={clsx('rmd-h2', className)}>{withInlineIcons(children)}</h2>
    ),
    h3: ({ children, className }) => (
      <h3 className={clsx('rmd-h3', className)}>{withInlineIcons(children)}</h3>
    ),
    h4: ({ children, className }) => (
      <h4 className={clsx('rmd-h4', className)}>{withInlineIcons(children)}</h4>
    ),
    h5: ({ children, className }) => (
      <h5 className={clsx('rmd-h5', className)}>{withInlineIcons(children)}</h5>
    ),
    h6: ({ children, className }) => (
      <h6 className={clsx('rmd-h5', className)}>{withInlineIcons(children)}</h6>
    ),

    p: ({ children, className }) => (
      <p className={className}>{withInlineIcons(children)}</p>
    ),

    a: ({ href, children }) => (
      <a href={href} target="_blank" rel="noreferrer" className="rmd-link">
        {children}
      </a>
    ),

    blockquote: ({ children }) => (
      <blockquote className="rmd-quote">{withInlineIcons(children)}</blockquote>
    ),

    ul: ({ children, className }) => <ul className={className}>{children}</ul>,
    ol: ({ children, className, start }) => <ol className={className} start={start}>{children}</ol>,
    li: ({ children, className }) => <li className={className}>{withInlineIcons(children)}</li>,

    // Report: ornamental divider between sections. Chat: a thin rule.
    hr: () => isReport ? <OrnamentalDivider className="rmd-ornament" /> : <hr className="rmd-hr" />,

    code: ({ className, children, ...rest }) => {
      const isInline = !(className || '').includes('language-')
      if (isInline) {
        return <code className="rmd-code-inline">{softBreakChildren(children)}</code>
      }
      // block code — passthrough; <pre> renderer will wrap it
      const { node: _node, ...safe } = rest as { node?: unknown }
      return <code className={className} {...(safe as object)}>{children}</code>
    },

    pre: ({ children }) => {
      // Detect mermaid blocks — the inner <code className="language-mermaid">
      const child: any = (children as any)?.props ? children : null
      const cls = (child?.props?.className as string) || ''
      const raw = String((child?.props?.children ?? '') as string).replace(/\n$/, '')
      const lang = cls.match(/language-([\w-]+)/)?.[1] ?? ''

      if (lang === 'mermaid' && raw.trim()) {
        return <Mermaid source={raw.trim()} />
      }

      return <CodeBlock lang={lang} raw={raw}>{children}</CodeBlock>
    },

    table: ({ children }) => (
      <div className="rmd-table-wrap">
        <table className="rmd-table">{children}</table>
      </div>
    ),
    th: ({ children, style }) => (
      <th style={style}>{withInlineIcons(softBreakChildren(children))}</th>
    ),
    td: ({ children, node, style }) => {
      let status: Status | null = null
      let short = false
      try {
        const text = hastText(node as HastLike).trim()
        status = statusOf(text)
        short = [...text].length <= 14
      } catch { status = null }
      return (
        <td style={style} className={status ? clsx('rmd-status', `rmd-status--${status}`, short && 'rmd-status--short') : undefined}>
          {/* Plain cells also get <wbr> after _ . / - (after the :icon: pass,
              so `:cloud-rain:` still matches) — "Owner/authorized" or a file
              name must not force the table wider than a narrow bubble. */}
          {status ? withInlineIcons(alignLeadingGlyph(children)) : softBreakChildren(withInlineIcons(children))}
        </td>
      )
    },

    strong: ({ children }) => (
      <strong className="rmd-strong">{withInlineIcons(children)}</strong>
    ),

    em: ({ children }) => (
      <em className="rmd-em">{withInlineIcons(children)}</em>
    ),
  }
}


// ── Code block: language label + copy button ───────────────────────────────
function CodeBlock({
  lang, raw, children,
}: {
  lang: string
  raw: string
  children: React.ReactNode
}) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(raw)
      setCopied(true)
      setTimeout(() => setCopied(false), 1400)
    } catch { /* silent */ }
  }
  return (
    <div className="rmd-codeblock group/code">
      <div className="rmd-codeblock__bar">
        <span className="rmd-codeblock__lang">{lang || 'code'}</span>
        <button
          type="button"
          onClick={copy}
          title={copied ? 'Copied!' : 'Copy code'}
          aria-label={copied ? 'Copied' : 'Copy code'}
          className={clsx('rmd-codeblock__copy', copied && 'is-copied')}
        >
          {copied
            ? <><Check className="w-3 h-3" /> copied</>
            : <><Copy  className="w-3 h-3" /> copy</>}
        </button>
      </div>
      <pre className="rmd-codeblock__pre">{children}</pre>
    </div>
  )
}
